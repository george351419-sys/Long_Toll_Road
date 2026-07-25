#!/usr/bin/env python3
"""Analyze the 413 unreconciled quarterly rows to understand why they failed."""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
RECONCILED = DATA / "quarterly-value-reconciled.csv"

def main():
    rows = []
    with open(RECONCILED) as f:
        for row in csv.DictReader(f):
            rows.append(row)

    unreconciled = [r for r in rows if r.get("numeric_reconciliation_status") != "candidate_reconciled_3plus_fields"]
    print(f"Quarterly rows: {len(rows)}")
    print(f"Unreconciled: {len(unreconciled)}")

    # By field match count
    match_dist = Counter()
    for r in unreconciled:
        match_dist[int(r.get("matched_field_count", "0"))] += 1
    print(f"\nMatch count distribution:")
    for k in sorted(match_dist.keys()):
        print(f"  {k} fields matched: {match_dist[k]}")

    # By field
    fields = ["revenue_yoy", "profit_yoy", "gross_margin", "eps", "bps", "ocf_per_share"]
    match_columns = {
        "revenue_yoy": "revenue_yoy_pdf_match",
        "profit_yoy": "profit_yoy_pdf_match",
        "gross_margin": "gross_margin_pdf_match",
        "eps": "eps_pdf_match",
        "bps": "bps_pdf_match",
        "ocf_per_share": "ocf_per_share_pdf_match",
    }
    field_failures = Counter()
    for r in unreconciled:
        for fname, col in match_columns.items():
            if r.get(col, "False") != "True":
                field_failures[fname] += 1
    print(f"\nFailed field matches (out of {len(unreconciled)}):")
    for fname, count in sorted(field_failures.items(), key=lambda x: -x[1]):
        print(f"  {fname}: {count} ({count/len(unreconciled)*100:.0f}%)")

    # By company
    by_company = Counter()
    by_exit = Counter()
    for r in unreconciled:
        by_company[r["code"]] += 1
        if r.get("candidate_exit", "False") == "True":
            by_exit[r["code"]] += 1

    print(f"\nCompanies with most unreconciled rows:")
    for code, count in by_company.most_common(10):
        exit_flag = " [EXIT]" if by_exit.get(code, 0) > 0 else ""
        print(f"  {code}: {count} rows{exit_flag}")

    # Exit signals in unreconciled
    exit_unreconciled = [r for r in unreconciled if r.get("candidate_exit", "False") == "True"]
    print(f"\nExit signals in unreconciled: {len(exit_unreconciled)}")

    # Check strict source link
    link_status = Counter()
    for r in unreconciled:
        link_status[r.get("strict_source_link", "?")] += 1
    print(f"\nStrict source link:")
    for k, v in sorted(link_status.items()):
        print(f"  {k}: {v}")

    # By year/report
    by_report = Counter()
    for r in unreconciled:
        year = r.get("report", "")[:4]
        by_report[year] += 1
    print(f"\nBy report year:")
    for y in sorted(by_report.keys()):
        print(f"  {y}: {by_report[y]}")

    summary = {
        "total_unreconciled": len(unreconciled),
        "match_distribution": dict(match_dist),
        "field_failures": dict(field_failures.most_common()),
        "top_companies": dict(by_company.most_common(10)),
        "exit_signals_unreconciled": len(exit_unreconciled),
        "generated_at": None,
    }
    print(f"\nSummary json:\n{json.dumps(summary, ensure_ascii=False, indent=2)}")


if __name__ == "__main__":
    main()
