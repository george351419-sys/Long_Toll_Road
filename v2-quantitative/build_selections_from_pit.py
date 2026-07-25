#!/usr/bin/env python3
"""
Build V0 backtest selections from PIT store data.
Uses verified quarterly fundamentals to:
  1) Extract annual (1231) metrics for each company-year
  2) Compute percentile ranks across the universe
  3) Score companies by financial quality
  4) Write selections.csv for the existing backtest engine

Only uses the 45 hot companies (the only ones with price data).
"""
from __future__ import annotations

import csv, json, math, sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import statistics

HERE = Path(__file__).resolve().parent
PIT_DB = HERE.parent / "industry_pit/complete-system-v1" / "data" / "pit-facts.sqlite"
OUTPUT = HERE / "data"
SELECTIONS = OUTPUT / "selections.csv"

HOT_CODES = [
    "000036","000048","000636","000663","000791","000799","000810",
    "000968","000975","001203","002032","002098","002128","002258",
    "002304","002432","002460","002466","002468","002508","002558",
    "002677","002739","002847","002963","300002","300080","300196",
    "300246","300343","300410","300450","300461","300531","300586",
    "300592","300618","300693","300735","300770","300785","300856",
    "300896","300979","301004",
]

COST = {"commission": 0.00025, "minimum_commission": 5, "slippage_bps": 5, "sell_stamp_tax": 0.001}


def load_pit_data() -> dict:
    """Load all quarterly fundamentals from PIT store."""
    conn = sqlite3.connect(PIT_DB)
    rows = conn.execute(
        """SELECT entity_id, effective_at, published_at, available_after,
                  value_json
           FROM facts
           WHERE entity_type='governance'
             AND metric_id='quarterly_fundamentals'
             AND verification_status='verified'
           ORDER BY entity_id, effective_at"""
    ).fetchall()
    conn.close()

    data = defaultdict(list)
    for r in rows:
        code, effective, published, available, value_str = r
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
            "available_after": (available or published)[:10],
            "report": effective.replace("-", ""),
            **metrics,
        })
    return dict(data)


def compute_percentile(values: list[float], value: float) -> float:
    """Compute percentile rank from 0-1."""
    if not values or value is None:
        return 0.5
    count_below = sum(1 for v in values if v < value)
    count_equal = sum(1 for v in values if v == value)
    return (count_below + 0.5 * count_equal) / len(values)


def main():
    print("Loading PIT quarterly data...")
    pit_data = load_pit_data()
    print(f"Companies loaded: {len(pit_data)}")
    
    # For annual selection, use reports ending in 1231 (annual reports)
    # The selection date is the available_after date + buffer
    annual_data = []  # (code, report_year, metrics, signal_date)
    
    for code, facts in pit_data.items():
        for fact in facts:
            report = fact.get("report", "")
            if not report.endswith("1231"):
                continue
            # Extract year
            year = int(report[:4])
            # Signal date: available_after + 1 day (or use published_at)
            signal = fact.get("available_after", fact.get("published_at", f"{year}-04-30"))
            if signal < f"{year}-04-01":
                signal = f"{year}-04-30"  # Conservative: annual reports due by April 30
            
            metrics = {k: fact.get(k) for k in ["revenue_yoy", "profit_yoy", "roe", "gross_margin", "ocf_per_share", "eps", "bps"]}
            annual_data.append((code, year, metrics, signal))

    if not annual_data:
        print("No annual (1231) data found!")
        return

    print(f"Annual data rows: {len(annual_data)}")
    print(f"Years: {min(y for _,y,_,_ in annual_data)} - {max(y for _,y,_,_ in annual_data)}")
    
    # For each year, compute percentile ranks
    selections = []
    signal_date_col = None
    
    for year in sorted(set(y for _, y, _, _ in annual_data)):
        year_data = [(c, y, m, s) for c, y, m, s in annual_data if y == year]
        if len(year_data) < 5:
            continue  # Need enough companies for meaningful percentiles
        
        # Collect raw values for percentile computation
        rev_vals = [m.get("revenue_yoy") for _, _, m, _ in year_data if m.get("revenue_yoy") is not None]
        profit_vals = [m.get("profit_yoy") for _, _, m, _ in year_data if m.get("profit_yoy") is not None]
        roe_vals = [m.get("roe") for _, _, m, _ in year_data if m.get("roe") is not None]
        gm_vals = [m.get("gross_margin") for _, _, m, _ in year_data if m.get("gross_margin") is not None]
        ocf_vals = [m.get("ocf_per_share") for _, _, m, _ in year_data if m.get("ocf_per_share") is not None]
        
        # Cap extreme values to avoid distortion
        def cap(vals, percentile=0.99):
            sorted_vals = sorted(vals)
            cap_val = sorted_vals[min(len(sorted_vals)-1, int(len(sorted_vals)*percentile))]
            floor_val = sorted_vals[max(0, int(len(sorted_vals)*(1-percentile)))]
            return cap_val, floor_val
        
        # Signal date: use the earliest signal date for this year
        signal_date = min(s for _, _, _, s in year_data).split("T")[0]
        if not signal_date_col:
            signal_date_col = signal_date
        
        for code, _, metrics, signal in year_data:
            rev = metrics.get("revenue_yoy")
            profit = metrics.get("profit_yoy")
            roe = metrics.get("roe")
            gm = metrics.get("gross_margin")
            ocf = metrics.get("ocf_per_share")
            eps = metrics.get("eps")
            bps = metrics.get("bps")
            
            # Percentile ranks
            pct_rev = compute_percentile(rev_vals, rev) if rev is not None else 0
            pct_profit = compute_percentile(profit_vals, profit) if profit is not None else 0
            pct_roe = compute_percentile(roe_vals, roe) if roe is not None else 0
            pct_gm = compute_percentile(gm_vals, gm) if gm is not None else 0
            pct_ocf = compute_percentile(ocf_vals, ocf) if ocf is not None else 0
            
            # Score: equal weights for all 5 factors
            score = (pct_rev + pct_profit + pct_roe + pct_gm + pct_ocf) / 5.0
            
            report = f"{year}1231"
            signal_dt = signal.split("T")[0] if "T" in signal else signal
            
            for top_n in [10, 15, 20]:
                selections.append({
                    "code": code,
                    "name": "",
                    "notice_date": signal_dt,
                    "update_date": signal_dt,
                    "report_date": report,
                    "revenue_yoy": rev or "",
                    "profit_yoy": profit or "",
                    "roe": roe or "",
                    "gross_margin": gm or "",
                    "ocf_per_share": ocf or "",
                    "industry": "",
                    "revision_version_verified": False,
                    "plain_code": code,
                    "code_name": code,
                    "pct_revenue_yoy": round(pct_rev, 4),
                    "pct_profit_yoy": round(pct_profit, 4),
                    "pct_roe": round(pct_roe, 4),
                    "pct_gross_margin": round(pct_gm, 4),
                    "pct_ocf_per_share": round(pct_ocf, 4),
                    "score": round(score, 4),
                    "report": report,
                    "signal_date": signal_dt,
                    "top_n": top_n,
                })

    if not selections:
        print("No selections generated!")
        return

    # Write selections.csv
    OUTPUT.mkdir(parents=True, exist_ok=True)
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
            row = {k: s.get(k, "") for k in fieldnames}
            if isinstance(row.get("revision_version_verified"), bool):
                row["revision_version_verified"] = str(row["revision_version_verified"])
            w.writerow(row)
    
    # Summary
    unique_codes = set(s["code"] for s in selections)
    years = set(s["report"][:4] for s in selections)
    scores_by_year = defaultdict(list)
    for s in selections:
        if s["top_n"] == 10:
            scores_by_year[s["report"][:4]].append(s["score"])
    
    print(f"\nSelections written: {SELECTIONS}")
    print(f"  Total rows: {len(selections)}")
    print(f"  Companies: {len(unique_codes)}")
    print(f"  Years: {', '.join(sorted(years))}")
    print(f"  Signal dates: {sorted(set(s['signal_date'] for s in selections))}")
    
    # Score ranges
    for year in sorted(scores_by_year):
        sc = scores_by_year[year]
        print(f"  {year}: {len(sc)} companies, score range {min(sc):.3f} - {max(sc):.3f}")


if __name__ == "__main__":
    main()
