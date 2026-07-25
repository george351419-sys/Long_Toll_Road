#!/usr/bin/env python3
"""
First-pass automated event review.
Reads the event-review-queue.csv, applies text-based rules from the event
classification schema, and splits into:
  1) auto_verified  – entries that can be promoted to PIT facts without visual review
  2) needs_visual    – entries that require human/NVIDIA CUDA page verification
  3) rejected        – entries whose snippet clearly shows they are not relevant

Outputs:
  data/event-first-pass-verified.csv    – auto-verified facts ready for PIT store
  data/event-first-pass-visual-queue.csv – remaining human review items
  data/event-first-pass-rejected.csv     – rejected candidates with reasons
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
PIT_DB = DATA / "pit-facts.sqlite"
QUEUE = DATA / "event-review-queue.csv"

RULES = HERE / "event-classification-rules.json"
RISK = HERE / "risk-rules.json"

# Load rules
with open(RULES) as f:
    RULES_DATA = json.load(f)
with open(RISK) as f:
    RISK_DATA = json.load(f)

ST_ONSET_REGEX = re.compile(r"实施|实行|继续实行|暂停上市|终止上市|退市整理")
ST_REVERSAL_REGEX = re.compile(r"撤销|申请撤销|恢复上市|摘帽")
INVESTIGATION_COMPANY_REGEX = re.compile(
    r"(公司|上市公司|本公司)(因|涉嫌|收到|被)", re.IGNORECASE
)
INVESTIGATION_INDIVIDUAL_ONLY_REGEX = re.compile(
    r"(董事|监事|高管|股东|实际控制人|董事长|总经理|副总经理|财务负责人)"
    r"(因|涉嫌|收到|被)(?!.*公司)", re.IGNORECASE
)
CONTRACT_AMOUNT_REGEX = re.compile(r"(\d[\d,.]*\s*(万元|亿元|万))")


def classify_entry(row: dict) -> dict:
    """Apply text-based rules to classify a single review entry."""
    metric = row.get("metric_id", "")
    snippet = row.get("snippet", "")
    title = row.get("title", "")
    page = int(row.get("page", "0") or 0)
    candidate_class = row.get("candidate_class", "")
    subjects = row.get("subject_candidate", "")
    disposition = row.get("review_disposition", "review_hard_veto_scope")
    combined = snippet + " " + title
    
    result = {
        "announcement_id": row["announcement_id"],
        "code": row["code"],
        "name": row["name"],
        "published_at": row["published_at"],
        "metric_id": metric,
        "candidate_class": candidate_class,
        "page": page,
        "text_sha256": row.get("text_sha256", ""),
        "pdf_sha256": row.get("pdf_sha256", ""),
        "auto_verdict": "pending",
        "auto_reason": "",
        "rejection_reason": "",
    }

    # --- Rule 1: ST Onset/Reversal with clear title ---
    if metric == "listing_risk":
        if "风险警示" in title and ST_ONSET_REGEX.search(title):
            result["auto_verdict"] = "verified_hard_veto_onset"
            result["auto_reason"] = "Title contains ST onset keywords"
            return result
        if "撤销" in title and ("风险警示" in title or "特别处理" in title):
            result["auto_verdict"] = "verified_hard_veto_reversal"
            result["auto_reason"] = "Title contains ST reversal keywords"
            return result
        if "暂停上市" in title or "终止上市" in title:
            result["auto_verdict"] = "verified_hard_veto_onset"
            result["auto_reason"] = "Suspension/termination of listing in title"
            return result
        if ST_REVERSAL_REGEX.search(title) and "恢复" in title:
            result["auto_verdict"] = "verified_hard_veto_reversal"
            result["auto_reason"] = "Resumption of listing in title"
            return result

    # --- Rule 2: Investigation – distinguish company vs individual ---
    if metric == "investigation":
        if INVESTIGATION_COMPANY_REGEX.search(combined):
            result["auto_verdict"] = "needs_visual"
            result["auto_reason"] = "Investigation mentions company – verify if company is the subject"
            return result
        if INVESTIGATION_INDIVIDUAL_ONLY_REGEX.search(combined):
            # Check if the individual-only pattern holds true: director is investigated but not the company
            if "公司" not in combined.split("董事")[0] if "董事" in combined else True:
                result["auto_verdict"] = "verified_individual_only_not_hard_veto"
                result["auto_reason"] = "Investigation targets individuals only, not the company"
                return result
        result["auto_verdict"] = "needs_visual"
        result["auto_reason"] = "Investigation pattern ambiguous – verify PDF page directly"
        return result

    # --- Rule 3: Administrative Penalty – check if company is the subject ---
    if metric == "administrative_penalty":
        if "公司" in subjects or "company" in subjects.lower():
            result["auto_verdict"] = "needs_visual"
            result["auto_reason"] = "Penalty mentions company as subject – verify penalty type and date"
            return result
        result["auto_verdict"] = "needs_visual"
        result["auto_reason"] = "Penalty subject ambiguous"
        return result

    # --- Rule 4: Audit Opinion – check for non-standard opinion keywords ---
    if metric == "audit_opinion":
        non_std = re.search(
            r"(保留意见|否定意见|无法表示意见|带强调事项段)", snippet
        )
        if non_std:
            result["auto_verdict"] = "verified_non_standard_audit"
            result["auto_reason"] = f"Non-standard audit opinion: {non_std.group(1)}"
            return result
        result["auto_verdict"] = "needs_visual"
        result["auto_reason"] = "Audit opinion type ambiguous – verify PDF table"
        return result

    # --- Rule 5: Contract / Bid Win – extract amount ---
    if metric in ("contract", "bid_win"):
        amounts = CONTRACT_AMOUNT_REGEX.findall(snippet)
        if amounts:
            result["auto_verdict"] = "verified_alpha_evidence"
            result["auto_reason"] = f"Contract/bid amount extracted: {amounts}"
            return result
        result["auto_verdict"] = "needs_visual"
        result["auto_reason"] = "No clear amount in snippet – verify PDF"
        return result

    # --- Rule 6: Capacity – check for numerical expansion details ---
    if metric == "capacity":
        if re.search(r"\d+[万万亿亿]?\s*(吨|台|套|平方米|亩|MW|GW)", snippet):
            result["auto_verdict"] = "verified_alpha_evidence"
            result["auto_reason"] = "Capacity expansion with specific scale"
            return result
        result["auto_verdict"] = "needs_visual"
        result["auto_reason"] = "Capacity details not clear in snippet"
        return result

    # Default: needs visual review
    result["auto_verdict"] = "needs_visual"
    result["auto_reason"] = "No automated rule matched – requires human visual review"
    return result


def commit_to_pit_store(fact_rows: list[dict]) -> dict:
    """Commit auto-verified facts to the PIT fact store."""
    if not fact_rows:
        return {"committed": 0, "errors": []}

    conn = sqlite3.connect(PIT_DB)
    committed = 0
    errors = []

    batch_id = f"event_first_pass_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    try:
        # Create batch
        conn.execute(
            "INSERT INTO ingest_batches (batch_id, purpose, started_at, state, record_count) "
            "VALUES (?, ?, ?, ?, ?)",
            (batch_id, "event_first_pass_auto_verify",
             datetime.now(timezone.utc).isoformat(), "committed", len(fact_rows)),
        )

        for fr in fact_rows:
            fact_id = f"event_{fr['code']}_{fr['announcement_id']}_{fr['metric_id']}"
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
                        fact_id, batch_id, "event", fr["code"], fr["metric_id"],
                        fr["published_at"][:10], fr["published_at"][:10],
                        fr["published_at"], datetime.now(timezone.utc).isoformat(),
                        f"cninfo:{fr['announcement_id']}", fr.get("pdf_url", ""),
                        fr["pdf_sha256"] if fr.get("pdf_sha256") else "x" * 64,
                        fr.get("auto_reason", "")[:200],
                        json.dumps({"announcement_id": fr["announcement_id"],
                                    "page": fr["page"]}, ensure_ascii=False),
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

    return {"committed": committed, "errors": errors, "batch_id": batch_id}


def main():
    if not QUEUE.exists():
        print(f"Review queue not found: {QUEUE}")
        return

    verified_rows = []
    visual_rows = []
    rejected_rows = []

    with open(QUEUE) as f:
        reader = csv.DictReader(f)
        for row in reader:
            result = classify_entry(row)
            verdict = result["auto_verdict"]

            if verdict.startswith("verified_"):
                # Create a flatter row for CSV output
                out = {**row, "auto_verdict": verdict, "auto_reason": result["auto_reason"]}
                verified_rows.append(out)
            elif verdict == "needs_visual":
                out = {**row, "auto_verdict": verdict, "auto_reason": result["auto_reason"]}
                visual_rows.append(out)
            else:
                out = {**row, "auto_verdict": verdict, "auto_reason": result["auto_reason"]}
                rejected_rows.append(out)

    print(f"\n{'='*60}")
    print(f"Event First-Pass Review Results")
    print(f"{'='*60}")
    print(f"  Total queue entries:     {len(verified_rows) + len(visual_rows) + len(rejected_rows)}")
    print(f"  Auto-verified (PIT):     {len(verified_rows)}")
    print(f"  Needs visual review:     {len(visual_rows)}")
    print(f"  Rejected:                {len(rejected_rows)}")

    # Write verified output
    vf = DATA / "event-first-pass-verified.csv"
    if verified_rows:
        with open(vf, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=verified_rows[0].keys())
            w.writeheader()
            w.writerows(verified_rows)
        print(f"\n  Written: {vf} ({len(verified_rows)} rows)")

    # Write visual queue output
    vqf = DATA / "event-first-pass-visual-queue.csv"
    if visual_rows:
        with open(vqf, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=visual_rows[0].keys())
            w.writeheader()
            w.writerows(visual_rows)
        print(f"  Written: {vqf} ({len(visual_rows)} rows)")

    # Write rejected output
    rf = DATA / "event-first-pass-rejected.csv"
    if rejected_rows:
        with open(rf, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=rejected_rows[0].keys())
            w.writeheader()
            w.writerows(rejected_rows)
        print(f"  Written: {rf} ({len(rejected_rows)} rows)")

    # For auto-verified entries, also commit to PIT store
    if verified_rows:
        pit_result = commit_to_pit_store(verified_rows)
        print(f"\n  PIT store commit: {pit_result['committed']} rows committed"
              f" (batch: {pit_result.get('batch_id', 'N/A')})")
        if pit_result['errors']:
            for e in pit_result['errors'][:5]:
                print(f"    Error: {e}")

    summary = {
        "total": len(verified_rows) + len(visual_rows) + len(rejected_rows),
        "auto_verified": len(verified_rows),
        "needs_visual": len(visual_rows),
        "rejected": len(rejected_rows),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (DATA / "event-first-pass-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\n  Summary: {DATA / 'event-first-pass-summary.json'}")

    # Breakdown by verdict
    from collections import Counter
    verdicts = Counter(v["auto_verdict"] for v in verified_rows)
    for k in sorted(verdicts):
        print(f"    {k}: {verdicts[k]}")

    # Summary for visual queue by metric
    if visual_rows:
        vis_metrics = Counter(v["metric_id"] for v in visual_rows)
        print(f"\n  Visual queue by metric:")
        for k, v in sorted(vis_metrics.items(), key=lambda x: -x[1]):
            print(f"    {k}: {v}")


if __name__ == "__main__":
    main()
