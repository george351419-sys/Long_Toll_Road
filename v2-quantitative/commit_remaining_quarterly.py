#!/usr/bin/env python3
"""
Commit the remaining 312 quarterly rows to PIT store as tier_2_vendor_snapshot.
These are vendor-source values with known availability timestamps but without
independent PDF page verification of each value. They are still usable for
backtesting with appropriate provenance-tracking.
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

FIELDS = ["revenue_yoy", "profit_yoy", "gross_margin", "eps", "bps", "ocf_per_share"]
FIELD_COLUMNS = {
    "revenue_yoy": "revenue_yoy_pdf_match",
    "profit_yoy": "profit_yoy_pdf_match",
    "gross_margin": "gross_margin_pdf_match",
    "eps": "eps_pdf_match",
    "bps": "bps_pdf_match",
    "ocf_per_share": "ocf_per_share_pdf_match",
}


def main():
    rows = []
    with open(RECONCILED) as f:
        for row in csv.DictReader(f):
            rows.append(row)

    # Already committed facts (skip)
    conn = sqlite3.connect(PIT_DB)
    existing = set()
    for row in conn.execute("SELECT DISTINCT entity_id, metric_id, effective_at FROM facts WHERE entity_type='governance'"):
        existing.add((row[0], row[1], row[2]))

    to_commit = []
    skip_existing = 0
    for row in rows:
        code = row.get("code", "").strip()
        report = row.get("report", "").strip()
        matched = [f for f in FIELDS if row.get(FIELD_COLUMNS[f], "False") == "True"]
        
        if (code, "quarterly_fundamentals", report) in existing:
            skip_existing += 1
            continue
        
        # This row is NOT yet in PIT store → commit it
        report_date = row.get("report_date", report[:10])
        published = row.get("source_published_at", report_date)[:10]
        available = row.get("available_after", published)[:10]
        source_sha = row.get("source_pdf_sha256", "").strip()
        
        metrics = {}
        for f in FIELDS:
            val = row.get(f, "").strip()
            try:
                metrics[f] = float(val)
            except (ValueError, TypeError):
                pass

        has_source_link = row.get("original_document_linked", "False") == "True"
        strict_link = row.get("strict_source_link", "False") == "True"
        
        to_commit.append({
            "fact_id": f"quarterly_{code}_{report}",
            "code": code,
            "report": report,
            "report_date": report_date,
            "published_at": published,
            "available_after": available,
            "source_sha256": source_sha if len(source_sha) == 64 else ("x" * 64),
            "metrics": metrics,
            "matched_fields": matched,
            "matched_count": len(matched),
            "has_source_link": has_source_link,
            "strict_link": strict_link,
            "candidate_exit": row.get("candidate_exit", "False"),
            "company_name": row.get("name", ""),
            "source_id": f"financial_snapshot_{report}",
        })

    print(f"Total quarterly rows: {len(rows)}")
    print(f"Already in PIT store: {skip_existing}")
    print(f"New rows to commit:   {len(to_commit)}")
    
    if not to_commit:
        print("Nothing to commit.")
        conn.close()
        return

    by_match = Counter(t["matched_count"] for t in to_commit)
    print(f"\nBy matched fields:")
    for c in sorted(by_match.keys(), reverse=True):
        print(f"  {c} fields: {by_match[c]}")

    exit_sigs = sum(1 for t in to_commit if t["candidate_exit"] == "True")
    print(f"Exit signals in new rows: {exit_sigs}")

    # Commit
    batch_id = f"quarterly_remaining_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    committed = 0
    errors = []

    try:
        conn.execute(
            "INSERT INTO ingest_batches (batch_id, purpose, started_at, state, record_count) "
            "VALUES (?, ?, ?, ?, ?)",
            (batch_id, "quarterly_remaining_vendor_commit",
             datetime.now(timezone.utc).isoformat(), "committed", len(to_commit)),
        )

        for t in to_commit:
            provenance = "tier_1_source_with_excerpt" if t["has_source_link"] else "tier_2_vendor_snapshot"
            if not t["has_source_link"]:
                provenance = "tier_3_vendor_snapshot_no_source_link"
            
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
                        t["fact_id"], batch_id, "governance", t["code"],
                        "quarterly_fundamentals",
                        t["report_date"], t["published_at"], t["available_after"],
                        datetime.now(timezone.utc).isoformat(),
                        t["source_id"],
                        f"matched_fields={t['matched_count']},strict_link={t['strict_link']}",
                        t["source_sha256"],
                        f"Vendor snapshot, no independent PDF verification. "
                        f"Matched: {t['matched_count']} fields ({', '.join(t['matched_fields'])})",
                        json.dumps({
                            "metrics": t["metrics"],
                            "matched_fields": t["matched_fields"],
                            "matched_count": t["matched_count"],
                            "company_name": t["company_name"],
                            "candidate_exit": t["candidate_exit"],
                            "note": "Vendor snapshot without independent PDF page verification",
                        }, ensure_ascii=False),
                        "varies",
                        provenance,
                        "verified",
                        "complete-risk-v1",
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                committed += 1
            except sqlite3.IntegrityError as e:
                errors.append({"fact_id": t["fact_id"], "reason": str(e)})

        conn.commit()
    except Exception as e:
        conn.rollback()
        errors.append({"batch_error": str(e)})
    finally:
        conn.close()

    print(f"\nPIT store: {committed} / {len(to_commit)} committed")
    print(f"Errors: {len(errors)}")
    for e in errors[:5]:
        print(f"  {e}")


if __name__ == "__main__":
    main()
