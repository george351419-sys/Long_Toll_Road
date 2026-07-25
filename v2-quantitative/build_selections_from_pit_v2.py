#!/usr/bin/env python3
"""
V2 selections builder: uses CONSISTENT annual signal dates for proper backtesting.
Instead of using each company's individual report publication date,
consolidates to a fixed annual rebalance date (May 1 each year).
"""
from __future__ import annotations

import csv, json, math, sqlite3
from collections import defaultdict
from pathlib import Path
import statistics

HERE = Path(__file__).resolve().parent
PIT_DB = HERE.parent / "industry_pit/complete-system-v1" / "data" / "pit-facts.sqlite"
SELECTIONS = HERE / "data" / "selections.csv"

HOT_CODES = [
    "000036","000048","000636","000663","000791","000799","000810",
    "000968","000975","001203","002032","002098","002128","002258",
    "002304","002432","002460","002466","002468","002508","002558",
    "002677","002739","002847","002963","300002","300080","300196",
    "300246","300343","300410","300450","300461","300531","300586",
    "300592","300618","300693","300735","300770","300785","300856",
    "300896","300979","301004",
]


def load_pit_data() -> dict:
    conn = sqlite3.connect(PIT_DB)
    rows = conn.execute(
        """SELECT entity_id, effective_at, published_at, value_json
           FROM facts
           WHERE entity_type='governance'
             AND metric_id='quarterly_fundamentals'
             AND verification_status='verified'
           ORDER BY entity_id, effective_at"""
    ).fetchall()
    conn.close()

    data = defaultdict(list)
    for r in rows:
        code, effective, published, value_str = r
        if code not in HOT_CODES:
            continue
        try:
            vals = json.loads(value_str)
        except (json.JSONDecodeError, TypeError):
            continue
        metrics = vals.get("metrics", {})
        data[code].append({
            "effective_at": effective,
            "published_at": published,
            **metrics,
        })
    return dict(data)


def main():
    print("Loading PIT quarterly data...")
    pit_data = load_pit_data()
    print(f"Companies loaded: {len(pit_data)}")

    # For each company, extract annual (1231) data
    annual_by_company = defaultdict(dict)
    for code, facts in pit_data.items():
        for fact in facts:
            report = fact.get("effective_at", "").replace("-", "")
            if not report.endswith("1231"):
                continue
            year = int(report[:4])
            metrics = {k: fact.get(k) for k in ["revenue_yoy", "profit_yoy", "roe", "gross_margin", "ocf_per_share"]}
            annual_by_company[code][year] = metrics

    print(f"Companies with annual data: {len(annual_by_company)}")
    
    # For each year, compute scores across ALL companies and select top N
    all_years = set()
    for code, years in annual_by_company.items():
        all_years.update(years.keys())
    
    selections = []
    
    for year in sorted(all_years):
        # Signal date: May 1 of the following year (annual reports due by April 30)
        signal_year = year + 1
        signal_date = f"{signal_year}-05-01"
        if signal_year > 2024:
            continue
        
        # Collect all companies with data for this year
        annual_scores = []
        for code in HOT_CODES:
            if code not in annual_by_company or year not in annual_by_company[code]:
                continue
            metrics = annual_by_company[code][year]
            
            rev = metrics.get("revenue_yoy")
            profit = metrics.get("profit_yoy")
            roe = metrics.get("roe")
            gm = metrics.get("gross_margin")
            ocf = metrics.get("ocf_per_share")
            
            annual_scores.append((code, metrics, rev, profit, roe, gm, ocf))
        
        if len(annual_scores) < 5:
            continue
        
        # Percentile ranks across ALL companies
        rev_vals = [x[2] for x in annual_scores if x[2] is not None]
        profit_vals = [x[3] for x in annual_scores if x[3] is not None]
        roe_vals = [x[4] for x in annual_scores if x[4] is not None]
        gm_vals = [x[5] for x in annual_scores if x[5] is not None]
        ocf_vals = [x[6] for x in annual_scores if x[6] is not None]
        
        def pct_of(vals, val):
            if not vals or val is None:
                return 0.5
            below = sum(1 for v in vals if v < val)
            equal = sum(1 for v in vals if v == val)
            return (below + 0.5 * equal) / len(vals)
        
        report = f"{year}1231"
        
        for code, metrics, rev, profit, roe, gm, ocf in annual_scores:
            pct_rev = pct_of(rev_vals, rev)
            pct_profit = pct_of(profit_vals, profit)
            pct_roe = pct_of(roe_vals, roe)
            pct_gm = pct_of(gm_vals, gm)
            pct_ocf = pct_of(ocf_vals, ocf)
            
            score = (pct_rev + pct_profit + pct_roe + pct_gm + pct_ocf) / 5.0
            
            for top_n in [10, 15, 20, 40]:
                selections.append({
                    "code": code,
                    "name": code,
                    "notice_date": signal_date,
                    "update_date": signal_date,
                    "report_date": report,
                    "revenue_yoy": rev or "",
                    "profit_yoy": profit or "",
                    "roe": roe or "",
                    "gross_margin": gm or "",
                    "ocf_per_share": ocf or "",
                    "industry": "",
                    "revision_version_verified": "False",
                    "plain_code": code,
                    "code_name": code,
                    "pct_revenue_yoy": round(pct_rev, 4),
                    "pct_profit_yoy": round(pct_profit, 4),
                    "pct_roe": round(pct_roe, 4),
                    "pct_gross_margin": round(pct_gm, 4),
                    "pct_ocf_per_share": round(pct_ocf, 4),
                    "score": round(score, 4),
                    "report": report,
                    "signal_date": signal_date,
                    "top_n": top_n,
                })

    if not selections:
        print("No selections generated!")
        return

    fieldnames = [
        "code", "name", "notice_date", "update_date", "report_date",
        "revenue_yoy", "profit_yoy", "roe", "gross_margin", "ocf_per_share",
        "industry", "revision_version_verified", "plain_code", "code_name",
        "pct_revenue_yoy", "pct_profit_yoy", "pct_roe", "pct_gross_margin",
        "pct_ocf_per_share", "score", "report", "signal_date", "top_n",
    ]
    with open(SELECTIONS, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for s in selections:
            w.writerow({k: s.get(k, "") for k in fieldnames})

    unique_codes = set(s["code"] for s in selections)
    years = sorted(set(s["report"][:4] for s in selections))
    signal_dates = sorted(set(s["signal_date"] for s in selections))
    
    print(f"\nSelections written: {SELECTIONS}")
    print(f"  Total rows: {len(selections)}")
    print(f"  Companies: {len(unique_codes)}")
    print(f"  Years: {', '.join(years)}")
    print(f"  Signal dates: {signal_dates}")
    
    by_year = defaultdict(list)
    for s in selections:
        if s["top_n"] == 20:
            by_year[s["report"][:4]].append(s["score"])
    for y in sorted(by_year):
        sc = by_year[y]
        print(f"  {y} (top20): {len(sc)} companies, scores {min(sc):.3f} - {max(sc):.3f}")


if __name__ == "__main__":
    main()
