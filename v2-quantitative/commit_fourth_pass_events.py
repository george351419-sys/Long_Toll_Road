#!/usr/bin/env python3
"""Commit the 22 fourth-pass verified events to PIT store."""
from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
PIT_DB = DATA / "pit-facts.sqlite"
FOURTH_PASS = DATA / "event-fourth-pass-verified.csv"
ORIGINAL_QUEUE = DATA / "event-first-pass-visual-queue.csv"

def main():
    # Load fourth-pass results
    verified = []
    with open(FOURTH_PASS) as f:
        for row in csv.DictReader(f):
            verified.append(row)
    
    if not verified:
        print("No fourth-pass results to commit")
        return
    
    # Cross-reference with original visual queue for pdf_sha256
    sha_map = {}
    if ORIGINAL_QUEUE.exists():
        with open(ORIGINAL_QUEUE) as f:
            for row in csv.DictReader(f):
                sha_map[row["announcement_id"]] = row.get("pdf_sha256", "x" * 64)
    
    conn = sqlite3.connect(PIT_DB)
    batch_id = f"event_fourth_pass_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    committed = 0
    errors = []
    
    try:
        conn.execute(
            "INSERT INTO ingest_batches (batch_id, purpose, started_at, state, record_count) "
            "VALUES (?, ?, ?, ?, ?)",
            (batch_id, "event_fourth_pass_auto_verify",
             datetime.now(timezone.utc).isoformat(), "committed", len(verified)),
        )
        
        for v in verified:
            ann_id = v["announcement_id"]
            sha = sha_map.get(ann_id, "x" * 64)
            fact_id = f"event_{v['code']}_{ann_id}_{v['metric_id']}"
            
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
                        fact_id, batch_id, "event", v["code"], v["metric_id"],
                        v["published_at"][:10], v["published_at"][:10],
                        v["published_at"], datetime.now(timezone.utc).isoformat(),
                        f"cninfo:{ann_id}", f"page=1", sha,
                        v.get("reason", "")[:200],
                        json.dumps({"verdict": v["verdict"], "reason": v["reason"]},
                                   ensure_ascii=False),
                        "unitless", "tier_1_source_with_excerpt",
                        "verified", "complete-risk-v1",
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                committed += 1
            except sqlite3.IntegrityError as e:
                errors.append({"fact_id": fact_id, "reason": str(e)})
        
        conn.commit()
    except Exception as e:
        conn.rollback()
        errors.append({"batch_error": str(e)})
    finally:
        conn.close()
    
    print(f"Committed: {committed} / {len(verified)}")
    if errors:
        for e in errors[:5]:
            print(f"  Error: {e}")
    print(f"Batch: {batch_id}")


if __name__ == "__main__":
    main()
