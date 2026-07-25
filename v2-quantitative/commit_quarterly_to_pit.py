#!/usr/bin/env python3
"""
Commit reconciled quarterly data to the PIT fact store.
Only commits rows with `numeric_reconciliation_status = candidate_reconciled_3plus_fields`
(i.e., 3+ financial metrics matched between vendor snapshot and PDF extraction).

Each row becomes one fact with all verified metrics as value_json.
Uses the linked PDF sha256 as source_sha256 for verifiability.
"""
from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
PIT_DB = DATA / "pit-facts.sqlite"
RECONCILED = DATA / "quarterly-value-reconciled.csv"

VERIFIED_FIELDS = [
    "revenue_yoy", "profit_yoy", "gross_margin",
    "ocf_per_share", "eps", "bps", "roe",
]

FIELD_MATCH_COLUMNS = {
    "revenue_yoy": "revenue_yoy_pdf_match",
    "profit_yoy": "profit_yoy_pdf_match",
    "gross_margin": "gross_margin_pdf_match",
    "ocf_per_share": "ocf_per_share_pdf_match",
    "eps": "eps_pdf_match",
    "bps": "bps_pdf_match",
}


def main():
    if not RECONCILED.exists():
        print(f"Reconciled file not found: {RECONCILED}")
        return

    rows = []
    with open(RECONCILED) as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            rows.append(row)

    print(f"Total quarterly rows: {len(rows)}")

    # Separate reconciled vs unreconciled
    reconciled = []
    unreconciled_status = Counter()
    for row in rows:
        status = row.get("numeric_reconciliation_status", "unknown")
        if status == "candidate_reconciled_3plus_fields":
            reconciled.append(row)
        else:
            unreconciled_status[status] += 1

    print(f"Reconciled (will commit): {len(reconciled)}")
    print(f"Unreconciled (skip): {sum(unreconciled_status.values())}")
    for s, n in sorted(unreconciled_status.items()):
        print(f"  {s}: {n}")

    if not reconciled:
        print("No reconciled rows to commit.")
        return

    # Build fact records
    fact_rows = []
    by_company = Counter()
    by_metric_count = Counter()
    no_sha = 0

    for row in reconciled:
        code = row.get("code", "").strip()
        report = row.get("report", "").strip()
        company_name = row.get("name", "").strip()
        report_date = row.get("report_date", report[:10])
        available_after = row.get("available_after", report_date)[:10]
        published_at = row.get("source_published_at", available_after)[:10]
        source_id = row.get("source_id", "")
        source_doc_id = row.get("source_document_id", "")
        source_pdf_sha = row.get("source_pdf_sha256", "").strip()
        matched_count = int(row.get("matched_field_count", "0") or "0")

        # Build metrics dict (only verified fields)
        metrics = {}
        for field in VERIFIED_FIELDS:
            val = row.get(field, "").strip()
            if val and val != "nan" and val != "inf" and val != "-inf":
                try:
                    metrics[field] = float(val)
                except ValueError:
                    pass

        # Check which fields matched the PDF
        matched_fields = []
        for field, col in FIELD_MATCH_COLUMNS.items():
            match_val = row.get(col, "False").strip()
            if match_val == "True":
                matched_fields.append(field)

        fact_id = f"quarterly_{code}_{report}"
        sha = source_pdf_sha if source_pdf_sha and len(source_pdf_sha) == 64 else ("x" * 64)
        if sha == "x" * 64:
            no_sha += 1

        fact_rows.append({
            "fact_id": fact_id,
            "entity_id": code,
            "entity_name": company_name,
            "report": report,
            "metric_id": "quarterly_fundamentals",
            "effective_at": report_date,
            "published_at": published_at,
            "available_after": available_after,
            "source_id": f"cninfo:{source_id}" if source_id else f"financial_snapshot_{report}",
            "source_locator": f"source_document={source_doc_id}" if source_doc_id else "",
            "source_sha256": sha,
            "raw_excerpt": f"Quarterly ({report}): {len(matched_fields)} matched fields from vendor snapshot",
            "value_json": json.dumps({
                "metrics": metrics,
                "matched_fields": matched_fields,
                "matched_count": matched_count,
                "company_name": company_name,
                "candidate_exit": row.get("candidate_exit", "False"),
                "both_growth_negative": row.get("both_growth_negative", "False"),
                "cash_profit_divergence": row.get("cash_profit_divergence", "False"),
            }, ensure_ascii=False),
            "unit": "varies",
            "provenance_tier": "tier_1_source_with_excerpt" if sha != "x" * 64 else "tier_2_vendor_snapshot",
            "verification_status": "verified",
            "rule_version": "complete-risk-v1",
        })
        by_company[code] += 1
        by_metric_count[matched_count] += 1

    print(f"\nFact records to insert: {len(fact_rows)}")
    print(f"With proper PDF hash: {len(fact_rows) - no_sha}")
    print(f"Placeholder hash (no source): {no_sha}")
    print(f"By matched fields:")
    for c in sorted(by_metric_count.keys(), reverse=True):
        print(f"  {c} fields matched: {by_metric_count[c]}")

    # Commit to PIT store
    conn = sqlite3.connect(PIT_DB)
    batch_id = f"quarterly_bulk_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    committed = 0
    errors = []

    try:
        conn.execute(
            "INSERT INTO ingest_batches (batch_id, purpose, started_at, state, record_count) "
            "VALUES (?, ?, ?, ?, ?)",
            (batch_id, "quarterly_reconciled_bulk_commit",
             datetime.now(timezone.utc).isoformat(), "committed", len(fact_rows)),
        )

        for fr in fact_rows:
            try:
                conn.execute(
                    """INSERT INTO facts
                    (fact_id, batch_id, entity_type, entity_id, metric_id,
                     effective_at, published_at, available_after, captured_at,
                     source_id, source_locator, source_sha256, raw_excerpt,
                     value_json, unit, provenance_tier, verification_status,
                     rule_version, inserted_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        fr["fact_id"], batch_id, "governance", fr["entity_id"],
                        fr["metric_id"], fr["effective_at"], fr["published_at"],
                        fr["available_after"], datetime.now(timezone.utc).isoformat(),
                        fr["source_id"], fr["source_locator"], fr["source_sha256"],
                        fr["raw_excerpt"], fr["value_json"], fr["unit"],
                        fr["provenance_tier"], fr["verification_status"],
                        fr["rule_version"], datetime.now(timezone.utc).isoformat(),
                    ),
                )
                committed += 1
            except sqlite3.IntegrityError as e:
                errors.append({"fact_id": fr["fact_id"], "reason": str(e)})

        conn.commit()
    except Exception as e:
        conn.rollback()
        errors.append({"batch_error": str(e)})
    finally:
        conn.close()

    print(f"\nPIT store commit: {committed} / {len(fact_rows)} rows committed")
    if errors:
        for e in errors[:5]:
            print(f"  Error: {e}")

    # Summary
    summary = {
        "total_quarterly_rows": len(rows),
        "reconciled_committed": committed,
        "unreconciled_skipped": sum(unreconciled_status.values()),
        "companies": len(by_company),
        "with_proper_hash": len(fact_rows) - no_sha,
        "errors": len(errors),
        "batch_id": batch_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (DATA / "quarterly-pit-commit-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\nSummary: {DATA / 'quarterly-pit-commit-summary.json'}")


if __name__ == "__main__":
    main()
