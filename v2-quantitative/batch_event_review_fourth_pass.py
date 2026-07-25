#!/usr/bin/env python3
"""
Fourth-pass event review: reads FULL extracted text for each remaining item,
tries more aggressive pattern matching, and handles the 72 remaining items.
"""
from __future__ import annotations

import csv
import gzip
import json
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
PIT_DB = DATA / "pit-facts.sqlite"
REMAINING = DATA / "event-third-pass-remaining-visual.csv"
EXTRACTED_EVENTS = HERE.parent / "extracted-events"
EXTRACTED_QUARTERLY = HERE.parent / "extracted-quarterly"

# Try multiple glob patterns to find the right text file
def read_text_aggressive(ann_id: str, code: str, year: str = "") -> str:
    """Try multiple locations to find the full extracted text."""
    # 1. extracted-events/{code}/{year}/
    base = EXTRACTED_EVENTS / code
    if base.exists():
        for gz in sorted(base.rglob(f"*{ann_id}*.txt.gz")):
            try:
                return gzip.open(gz, "rt", errors="replace").read(10000)
            except: pass
        for gz in sorted(base.rglob("*.txt.gz")):
            try:
                text = gzip.open(gz, "rt", errors="replace").read(5000)
                if ann_id[:8] in text:
                    return text
            except: pass
    
    # 2. extracted-quarterly/{code}/
    base2 = EXTRACTED_QUARTERLY / code
    if base2.exists():
        for gz in sorted(base2.rglob("*.txt.gz")):
            try:
                text = gzip.open(gz, "rt", errors="replace").read(5000)
                if ann_id[:8] in text:
                    return text
            except: pass
    
    return ""


# Aggressive amount patterns
ANY_AMOUNT_PATTERN = re.compile(
    r"(\d[\d,.]*)\s*(万?[元亿]|美元|欧元)",
    re.IGNORECASE,
)
CONTRACT_KEYWORDS = re.compile(
    r"(合同|中标|订单|签约|合作|协议).{0,200}?"
    r"(\d[\d,.]*\s*(万?[元亿]|美元|欧元))",
    re.IGNORECASE,
)
CAPACITY_KEYWORDS = re.compile(
    r"(产能|产量|生产线|车间|工厂).{0,200}?"
    r"(\d[\d,.]*\s*(万?[吨台套只盒支]|平方米|亩))",
    re.IGNORECASE,
)

def classify_v4(row: dict) -> dict:
    metric = row.get("metric_id", "")
    title = row.get("title", "")
    subjects = row.get("subjects", "")
    ann_id = row.get("announcement_id", "")
    code = row.get("code", "")
    
    result = {
        "code": code,
        "name": row.get("name", ""),
        "announcement_id": ann_id,
        "published_at": row.get("published_at", ""),
        "metric_id": metric,
        "title": title,
        "verdict": "pending",
        "reason": "",
    }
    
    full = read_text_aggressive(ann_id, code)
    combined = title + " " + full
    
    if metric in ("contract", "bid_win"):
        # Try contract keywords
        m = CONTRACT_KEYWORDS.search(combined)
        if m:
            result["verdict"] = "verified_alpha_evidence"
            result["reason"] = f"Contract/amount found: {m.group(2)}"
            return result
        # Generic amount in bid/contract context
        if "中标" in combined and "金额" in combined:
            m = ANY_AMOUNT_PATTERN.search(combined[combined.find("中标"):combined.find("中标")+500])
            if m:
                result["verdict"] = "verified_alpha_evidence"
                result["reason"] = f"Bid amount: {m.group(1)}{m.group(2)}"
                return result
        if "合同" in title and "日常经营" in title:
            m = ANY_AMOUNT_PATTERN.search(combined[combined.find("合同"):combined.find("合同")+300])
            if m:
                result["verdict"] = "verified_alpha_evidence"
                result["reason"] = f"Contract amount: {m.group(1)}{m.group(2)}"
                return result
        result["verdict"] = "needs_visual"
        return result
    
    if metric == "capacity":
        m = CAPACITY_KEYWORDS.search(combined)
        if m:
            result["verdict"] = "verified_alpha_evidence"
            result["reason"] = f"Capacity detail: {m.group(2)}"
            return result
        result["verdict"] = "needs_visual"
        return result
    
    if metric == "administrative_penalty":
        # Check if company penalty
        if re.search(r"(公司|本公司).{0,20}?处罚", combined):
            result["verdict"] = "verified_hard_veto_onset"
            result["reason"] = "Full text confirms company penalty"
            return result
        result["verdict"] = "needs_visual"
        result["reason"] = "Penalty subject unclear even in full text"
        return result
    
    if metric == "investigation":
        if "公司" in combined[:300] and "调查" in combined[:300]:
            result["verdict"] = "verified_hard_veto_onset"
            result["reason"] = "Full text confirms company investigation"
            return result
        result["verdict"] = "needs_visual"
        return result
    
    if metric == "audit_opinion":
        result["verdict"] = "needs_visual"
        return result
    
    result["verdict"] = "needs_visual"
    return result


def main():
    if not REMAINING.exists():
        print("Nothing to process")
        return
    
    results = []
    with open(REMAINING) as f:
        for row in csv.DictReader(f):
            results.append(classify_v4(row))
    
    verified = [r for r in results if r["verdict"].startswith("verified_")]
    visual = [r for r in results if r["verdict"] == "needs_visual"]
    
    verdicts = Counter(r["verdict"] for r in results)
    
    print(f"Total remaining: {len(results)}")
    print(f"Newly verified:  {len(verified)}")
    print(f"Still visual:    {len(visual)}")
    print(f"\nVerdicts:")
    for v, n in sorted(verdicts.items(), key=lambda x: -x[1]):
        print(f"  {v:35s} {n}")
    
    if visual:
        vm = Counter(v["metric_id"] for v in visual)
        print(f"\nRemaining by metric:")
        for m, n in sorted(vm.items(), key=lambda x: -x[1]):
            print(f"  {m:25s} {n}")
    
    # Commit verified
    if verified:
        vf = DATA / "event-fourth-pass-verified.csv"
        keys = ["code", "name", "announcement_id", "published_at", "metric_id", "title", "verdict", "reason"]
        with open(vf, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            for r in verified:
                w.writerow({k: r.get(k, "") for k in keys})
        print(f"\nVerified: {vf}")
    
    if visual:
        rv = DATA / "event-fourth-pass-remaining.csv"
        keys = ["code", "name", "announcement_id", "published_at", "metric_id", "title", "subjects", "verdict", "reason"]
        with open(rv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            for r in visual:
                w.writerow({k: r.get(k, "") for k in keys})
        print(f"Remaining visual: {rv}")
    
    summary = {"total": len(results), "verified": len(verified), "visual": len(visual),
               "verdicts": dict(verdicts), "generated_at": datetime.now(timezone.utc).isoformat()}
    (DATA / "event-fourth-pass-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nSummary saved.")


if __name__ == "__main__":
    main()
