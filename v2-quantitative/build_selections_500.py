#!/usr/bin/env python3
"""
Build selections for all 500 cohort companies using the 43 financial snapshots.
Also computes approximate track/toll scores for the full universe.

Outputs:
  data/selections_full.csv - V0 selections (all 500 cos, top 20 by quality score)
  data/selections_full_v1.csv - V1 selections (track-gate filtered, top 20)
  data/selections_full_v2.csv - V2 selections (track+toll filtered, top 20)
  data/500-coverage-audit.json - how many companies have what data
"""
from __future__ import annotations

import csv, json, statistics
from collections import defaultdict
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
SNAPSHOTS = HERE / "data" / "financial_snapshots"
COHORT = HERE.parent / "industry_pit" / "cohort" / "cohort-v1.csv"
PRICES_DIR = HERE / "data" / "prices"

# Load cohort
cohort = pd.read_csv(COHORT, dtype={"code": str})
cohort["code"] = cohort.code.str.zfill(6)
cohort_codes = set(cohort.code)

# Load industry labels
industry_of = dict(zip(cohort.code, cohort.industry_at_last_seen))

# Column mapping for snapshot data
COL_MAP = {
    "YSTZ": "revenue_yoy",
    "SJLTZ": "profit_yoy",
    "WEIGHTAVG_ROE": "roe",
    "XSMLL": "gross_margin",
    "MGJYXJJE": "ocf_per_share",
    "BASIC_EPS": "eps",
    "BPS": "bps",
}

# Load all annual (1231) snapshots
def load_annual_data():
    """Load annual data for all cohort companies from all snapshots."""
    all_rows = []
    for snap_path in sorted(SNAPSHOTS.glob("20*.csv")):
        year = snap_path.stem[:4]
        if int(year) < 2014 or int(year) > 2024:
            continue
        for chunk in pd.read_csv(snap_path, dtype={"SECURITY_CODE": str}, chunksize=50000):
            chunk = chunk[chunk.SECURITY_CODE.isin(cohort_codes)]
            if chunk.empty:
                continue
            chunk["code"] = chunk.SECURITY_CODE.str.zfill(6)
            # Only keep annual reports (December 31)
            chunk["report_date"] = pd.to_datetime(chunk["REPORTDATE"], errors="coerce")
            chunk = chunk[chunk["report_date"].dt.month == 12]
            if chunk.empty:
                continue
            chunk["year"] = chunk["report_date"].dt.year.astype(int)
            chunk["notice_date"] = pd.to_datetime(chunk["NOTICE_DATE"], errors="coerce")
            chunk["available_after"] = chunk["notice_date"].dt.strftime("%Y-%m-%d")
            
            for snap_col, metric in COL_MAP.items():
                if snap_col in chunk.columns:
                    chunk[metric] = pd.to_numeric(chunk[snap_col], errors="coerce")
            
            all_rows.append(chunk[["code", "year", "available_after"] + list(COL_MAP.values())].copy())
    
    return pd.concat(all_rows, ignore_index=True) if all_rows else pd.DataFrame()

print("Loading financial snapshots for all 500 cohort companies...")
annual = load_annual_data()
print(f"  Annual rows: {len(annual)}")
print(f"  Companies: {annual.code.nunique()}")
print(f"  Years: {annual.year.min()} - {annual.year.max()}")
print(f"  Coverage density: {annual.groupby('year').code.nunique().to_dict()}")

# Check price data availability
print("\nChecking price data availability...")
price_codes = set()
for f in PRICES_DIR.glob("*.csv"):
    c = f.stem
    if c not in ("CSI800", "CSI300"):
        price_codes.add(c)
cohort_with_prices = cohort_codes & price_codes
print(f"  Cohort companies with prices: {len(cohort_with_prices)}")

# Compute track scores for each industry
print("\nComputing industry track scores...")
with_prices_count = len(cohort_with_prices)

# Group by industry
by_industry = defaultdict(lambda: defaultdict(list))
by_company = defaultdict(lambda: defaultdict(dict))

for _, row in annual.iterrows():
    code = row["code"]
    year = row["year"]
    ind = industry_of.get(code, "未知")
    for col in COL_MAP.values():
        val = row.get(col)
        if pd.notna(val):
            by_industry[ind][col].append(val)
            if code not in by_company:
                by_company[code] = {}
            if year not in by_company[code]:
                by_company[code][year] = {}
            by_company[code][year][col] = val

track_scores = {}
for ind, data in by_industry.items():
    revs = data.get("revenue_yoy", [])
    margins = data.get("gross_margin", [])
    if len(revs) < 5 or len(margins) < 3:
        continue
    med_rev = statistics.median(revs)
    med_gm = statistics.median(margins)
    rev_std = statistics.stdev(revs) if len(revs) > 1 else 1
    gm_std = statistics.stdev(margins) if len(margins) > 1 else 1
    # Track score: higher = better industry dynamics
    track_scores[ind] = max(med_rev, -50) / max(rev_std, 0.1) * max(med_gm, 0) / max(gm_std, 0.1) / 5

# Compute toll scores for each company
print("Computing company toll scores...")
toll_scores = {}
for code, years in by_company.items():
    gms = []
    roes = []
    for y, m in years.items():
        if "gross_margin" in m:
            gms.append(m["gross_margin"])
        if "roe" in m:
            roes.append(m["roe"])
    if len(gms) < 3:
        toll_scores[code] = 0.0
        continue
    avg_gm = statistics.mean(gms)
    gm_std = statistics.stdev(gms) if len(gms) > 1 else 99
    avg_roe = statistics.mean(roes) if roes else 0
    margin_score = max(avg_gm, 0) / 40.0
    stability_score = min(50 / max(gm_std, 0.1), 10)
    roe_score = min(max(avg_roe / 15.0, 0), 3)
    toll_scores[code] = margin_score * stability_score * roe_score

# Build selections for each year
print("Generating selections...")
passing_inds = {ind for ind, s in track_scores.items() if s >= 0}
years = sorted(annual.year.unique())

fieldnames = [
    "code", "name", "notice_date", "update_date", "report_date",
    "revenue_yoy", "profit_yoy", "roe", "gross_margin", "ocf_per_share",
    "industry", "revision_version_verified", "plain_code", "code_name",
    "pct_revenue_yoy", "pct_profit_yoy", "pct_roe", "pct_gross_margin",
    "pct_ocf_per_share", "score", "report", "signal_date", "top_n",
]

def build_selections_for_year(year, codes_filter, signal_date):
    """Build selections for a specific year and company set."""
    ys = int(year)
    candidates = []
    for code in codes_filter:
        if code in by_company and ys in by_company[code]:
            m = by_company[code][ys]
            rev = m.get("revenue_yoy")
            profit = m.get("profit_yoy")
            roe = m.get("roe")
            gm = m.get("gross_margin")
            if rev is not None:
                candidates.append((code, rev, profit, roe, gm, by_company[code][ys]))
    
    if len(candidates) < 10:
        return []
    
    revs = [c[1] for c in candidates if c[1] is not None]
    profits = [c[2] for c in candidates if c[2] is not None]
    roes = [c[3] for c in candidates if c[3] is not None]
    gms = [c[4] for c in candidates if c[4] is not None]
    
    def pct(vals, val):
        if not vals or val is None: return 0.5
        return sum(1 for v in vals if v < val)/len(vals) + 0.5*sum(1 for v in vals if v == val)/len(vals)
    
    report = f"{ys}1231"
    rows = []
    for code, rev, profit, roe, gm, metrics in candidates:
        score = (pct(revs, rev) + pct(profits, profit) + pct(roes, roe) + pct(gms, gm)) / 4.0
        rows.append({
            "code": code, "name": code, "notice_date": signal_date,
            "update_date": signal_date, "report_date": report,
            "revenue_yoy": rev or "", "profit_yoy": profit or "",
            "roe": roe or "", "gross_margin": gm or "",
            "ocf_per_share": metrics.get("ocf_per_share", ""),
            "industry": industry_of.get(code, ""),
            "revision_version_verified": "False", "plain_code": code,
            "code_name": code,
            "pct_revenue_yoy": round(pct(revs, rev), 4),
            "pct_profit_yoy": round(pct(profits, profit), 4),
            "pct_roe": round(pct(roes, roe), 4),
            "pct_gross_margin": round(pct(gms, gm), 4),
            "pct_ocf_per_share": round(pct(revs, metrics.get("ocf_per_share")), 4),
            "score": round(score, 4),
            "report": report, "signal_date": signal_date, "top_n": 20,
        })
    return rows

def write_selections(fname, all_rows):
    path = HERE / "data" / fname
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in all_rows:
            w.writerow(r)
    codes = set(r["code"] for r in all_rows)
    print(f"  {fname:30s}: {len(all_rows)} rows, {len(codes)} companies")
    return len(codes)

# Build selections
v0_all, v1_all, v2_all = [], [], []

for year in sorted(years):
    signal = f"{int(year)+1}-05-01"
    if int(year) + 1 > 2024:
        continue
    
    # V0: all companies
    v0 = build_selections_for_year(year, cohort_codes, signal)
    v0_all.extend(v0)
    
    # V1: track-passing industries only
    v1_codes = {c for c in cohort_codes if industry_of.get(c, "") in passing_inds}
    v1 = build_selections_for_year(year, v1_codes, signal)
    v1_all.extend(v1)
    
    # V2: track + toll passing
    v2_codes = {c for c in v1_codes if toll_scores.get(c, 0) >= 1.5}
    v2 = build_selections_for_year(year, v2_codes, signal)
    v2_all.extend(v2)

print(f"\nSelection results:")
n0 = write_selections("selections_full_v0.csv", v0_all)
n1 = write_selections("selections_full_v1.csv", v1_all)
n2 = write_selections("selections_full_v2.csv", v2_all)

# Summary
audit = {
    "cohort_companies": len(cohort_codes),
    "with_annual_data": annual.code.nunique(),
    "years": sorted(int(y) for y in years),
    "with_price_data": len(cohort_with_prices),
    "industries": len(track_scores),
    "industry_pass_track": len(passing_inds),
    "v0_companies_selected": n0,
    "v1_companies_selected": n1,
    "v2_companies_selected": n2,
    "needs_price_download": len(cohort_codes) - len(cohort_with_prices),
    "generated_at": None,
}
(HERE / "data" / "selections-500-audit.json").write_text(
    json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)

print(f"\nSummary: {audit}")
