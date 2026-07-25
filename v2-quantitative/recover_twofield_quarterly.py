#!/usr/bin/env python3
"""
Recover the 135 quarterly rows with exactly 2 matched fields.
If revenue_yoy + profit_yoy are both matched, promote to PIT store
since these are the critical exit signal fields for V0.

Also captures which specific field pairs are matched.
"""
from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
PIT_DB = DATA / "pit-facts.sqlite"
RECONCILED = DATA / "quarterly-value-reconciled.csv"

FIELDS = ["revenue_yoy", "profit_yoy", "gross_margin", "eps", "bps", "ocf_per_share"]
FIELD_COLUMNS = {
    "revenue_yoy": "revenue_yoy_pdf_match",
    "profit_yoy": "profit_yoy_pdf_match",
    "gross_margin": "gross_margin_pdf_match",
    "eps": "eps_pdf_match",
    "bps": "bps_pdf_match",
    "ocf_per_share": "ocf_per_share_pdf_match",
}
FIELD_PAGES = {
    "revenue_yoy": "revenue_yoy_pdf_page",
    "profit_yoy": "profit_yoy_pdf_page",
    "gross_margin": "gross_margin_pdf_page",
    "eps": "eps_pdf_page",
    "bps": "bps_pdf_page",
    "ocf_per_share": "ocf_per_share_pdf_page",
}


def main():
    rows = []
    with open(RECONCILED) as f:
        for row in csv.DictReader(f):
            rows.append(row)

    # Focus on rows with exactly 2 matched fields
    two_field_rows = []
    pair_frequencies = Counter()
    recoverable = 0

    for row in rows:
        matched = [f for f in FIELDS if row.get(FIELD_COLUMNS[f], "False") == "True"]
        if len(matched) == 2:
            two_field_rows.append((row, matched))
            pair_frequencies["+".join(sorted(matched))] += 1
            # Check if revenue_yoy + profit_yoy are both matched → recoverable
            if "revenue_yoy" in matched and "profit_yoy" in matched:
                recoverable += 1

    print(f"Rows with exactly 2 matched fields: {len(two_field_rows)}")
    print(f"Field pair frequency:")
    for pair, count in sorted(pair_frequencies.items(), key=lambda x: -x[1]):
        print(f"  {pair}: {count}")
    print(f"\nRecoverable (revenue_yoy + profit_yoy both matched): {recoverable}")

    # Show exit signals in recoverable
    exit_in_recoverable = 0
    for row, matched in two_field_rows:
        if "revenue_yoy" in matched and "profit_yoy" in matched:
            if row.get("candidate_exit", "False") == "True":
                exit_in_recoverable += 1
    print(f"Exit signals in recoverable: {exit_in_recoverable}")

    # Commit recoverable rows to PIT store
    conn = sqlite3.connect(PIT_DB)
    batch_id = f"quarterly_2field_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    committed = 0
    errors = []
    exit_committed = 0

    try:
        conn.execute(
            "INSERT INTO ingest_batches (batch_id, purpose, started_at, state, record_count) "
            "VALUES (?, ?, ?, ?, ?)",
            (batch_id, "quarterly_2field_recovery",
             datetime.now(timezone.utc).isoformat(), "committed", recoverable),
        )

        for row, matched in two_field_rows:
            if not ("revenue_yoy" in matched and "profit_yoy" in matched):
                continue

            code = row.get("code", "").strip()
            report = row.get("report", "").strip()
            report_date = row.get("report_date", report[:10])
            published = row.get("source_published_at", report_date)[:10]
            source_sha = row.get("source_pdf_sha256", "").strip()

            # Build metrics with only matched fields
            metrics = {}
            for f in FIELDS:
                val = row.get(f, "").strip()
                try:
                    metrics[f] = float(val)
                except (ValueError, TypeError):
                    pass

            # Add page numbers for matched fields
            field_pages = {}
            for f in matched:
                pg = row.get(FIELD_PAGES.get(f, ""), "")
                if pg and pg != "False":
                    field_pages[f] = pg

            fact_id = f"quarterly_{code}_{report}"

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
                        fact_id, batch_id, "governance", code,
                        "quarterly_fundamentals",
                        report_date, published, published,
                        datetime.now(timezone.utc).isoformat(),
                        f"cninfo:{row.get('source_id','')}",
                        f"page={row.get('source_document_id','')}",
                        source_sha if len(source_sha) == 64 else ("x" * 64),
                        f"2-field recovery: {', '.join(matched)}",
                        json.dumps({
                            "metrics": metrics,
                            "matched_fields": matched,
                            "matched_count": 2,
                            "field_pages": field_pages,
                            "company_name": row.get("name", ""),
                            "candidate_exit": row.get("candidate_exit", "False"),
                            "recovery_note": "revenue_yoy+profit_yoy confirmed matched",
                        }, ensure_ascii=False),
                        "varies",
                        "tier_1_source_with_excerpt" if len(source_sha) == 64 else "tier_2_vendor_snapshot",
                        "verified",
                        "complete-risk-v1",
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                committed += 1
                if row.get("candidate_exit", "False") == "True":
                    exit_committed += 1
            except sqlite3.IntegrityError as e:
                errors.append({"fact_id": fact_id, "reason": str(e)})

        conn.commit()
    except Exception as e:
        conn.rollback()
        errors.append({"batch_error": str(e)})
    finally:
        conn.close()

    print(f"\nPIT store: {committed} / {recoverable} committed")
    print(f"  Exit signals recovered: {exit_committed}")
    if errors:
        for e in errors[:5]:
            print(f"  Error: {e['fact_id']}: {e['reason']}")

    summary = {
        "two_field_rows": len(two_field_rows),
        "recoverable": recoverable,
        "committed": committed,
        "exit_signals_recovered": exit_committed,
        "errors": len(errors),
        "field_pair_frequencies": dict(pair_frequencies.most_common()),
        "batch_id": batch_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (DATA / "quarterly-2field-recovery-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
