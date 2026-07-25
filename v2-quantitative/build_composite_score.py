#!/usr/bin/env python3
"""
Build composite score: track(30%) + toll(40%) + valuation(20%) + quality(10%)
Generates selections with the new score and runs backtest.
"""
from __future__ import annotations

import csv, json, statistics, math
from collections import defaultdict
from pathlib import Path
import pandas as pd

HERE = Path(__file__).resolve().parent
SNAPSHOTS = HERE / "data" / "financial_snapshots"
PRICES = HERE / "data" / "prices"
COHORT = HERE.parent / "industry_pit" / "cohort" / "cohort-v1.csv"

# Load cohort
cohort = pd.read_csv(COHORT, dtype={"code": str})
cohort["code"] = cohort.code.str.zfill(6)
ind_of = dict(zip(cohort.code, cohort.industry_at_last_seen))

# --- Step 1: Load ALL financial data from snapshots ---
all_fin = []
for snap in sorted(SNAPSHOTS.glob("20*.csv")):
    df = pd.read_csv(snap, dtype={"SECURITY_CODE": str, "NOTICE_DATE": str})
    df = df[df.SECURITY_CODE.isin(set(cohort.code))]
    if df.empty: continue
    df["code"] = df.SECURITY_CODE.str.zfill(6)
    df["report_date"] = pd.to_datetime(df.REPORTDATE, errors="coerce")
    df = df[df.report_date.dt.month == 12]  # annual only
    cols = ["code", "report_date", "NOTICE_DATE", "YSTZ", "SJLTZ", "WEIGHTAVG_ROE", "XSMLL", "BASIC_EPS", "BPS"]
    keep = [c for c in cols if c in df.columns]
    df = df[keep].copy()
    for c in ["YSTZ", "SJLTZ", "WEIGHTAVG_ROE", "XSMLL", "BASIC_EPS", "BPS"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    all_fin.append(df)

all_fin = pd.concat(all_fin, ignore_index=True) if all_fin else pd.DataFrame()
all_fin["year"] = all_fin.report_date.dt.year
print(f"Annual data: {len(all_fin)} rows, {all_fin.code.nunique()} companies")

# --- Step 2: Compute INDUSTRY track scores (structural characteristics) ---
ind_data = defaultdict(lambda: {"gms": [], "revs": [], "codes": set()})
for _, r in all_fin.iterrows():
    ind = ind_of.get(r.code, "?")
    ind_data[ind]["gms"].append(r.XSMLL) if pd.notna(r.XSMLL) else None
    ind_data[ind]["revs"].append(r.YSTZ) if pd.notna(r.YSTZ) else None
    ind_data[ind]["codes"].add(r.code)

track_scores = {}
for ind, d in ind_data.items():
    gms = [x for x in d["gms"] if x is not None]
    revs = [x for x in d["revs"] if x is not None]
    if len(gms) < 3: continue
    # Track score: high margins + stable + consistent growth
    med_gm = statistics.median(gms)
    gm_stab = 50 / max(statistics.stdev(gms), 0.1)
    med_rev = statistics.median(revs) if revs else 0
    rev_stab = 50 / max(statistics.stdev(revs) if len(revs) > 1 else 1, 0.1)
    conc = 50 / max(len(d["codes"]), 1)  # fewer companies = more concentrated
    track_scores[ind] = max(med_gm, 0)/40 * gm_stab/10 + max(med_rev, 0)/20 * rev_stab/10 + conc/10

# --- Step 3: Compute COMPANY toll scores (competitive advantage) ---
comp_data = defaultdict(lambda: {"gms": [], "roes": [], "revs": []})
for _, r in all_fin.iterrows():
    comp_data[r.code]["gms"].append(r.XSMLL) if pd.notna(r.XSMLL) else None
    comp_data[r.code]["roes"].append(r.WEIGHTAVG_ROE) if pd.notna(r.WEIGHTAVG_ROE) else None
    comp_data[r.code]["revs"].append(r.YSTZ) if pd.notna(r.YSTZ) else None

toll_scores = {}
for code, d in comp_data.items():
    gms = d["gms"]
    if len(gms) < 3: toll_scores[code] = 0; continue
    avg_gm = statistics.mean(gms)
    gm_stab = 50 / max(statistics.stdev(gms), 0.1)
    avg_roe = statistics.mean(d["roes"]) if d["roes"] else 0
    toll_scores[code] = max(avg_gm, 0)/40 * min(gm_stab, 10) * min(max(avg_roe/15, 0), 3)

# --- Step 4: Load price data and compute valuation scores ---
print("Loading price data for valuation...")
price_cache = {}  # code -> DataFrame(date, close)
for f in PRICES.glob("*.csv"):
    c = f.stem
    if c in ("CSI800","CSI300"): continue
    try:
        df = pd.read_csv(f, usecols=["date","close"])
        df["date"] = pd.to_datetime(df.date)
        price_cache[c] = df.set_index("date")["close"]
    except: pass

SELECTION_DATES = [f"{y}-05-01" for y in range(2016, 2025)]

def get_price_near(code, target_date):
    """Get the closest price to target_date (handles holidays)."""
    if code not in price_cache: return None
    p = price_cache[code]
    d = pd.Timestamp(target_date)
    for offset in range(10):
        for sign in [1, -1]:
            test = d + pd.Timedelta(days=offset*sign)
            if test in p.index:
                return p[test]
    return None

# For each year, compute valuation percentiles
val_by_year = {}
for year in range(2015, 2024):
    # EPS from this year's annual report (reported this year, available next year)
    yr = year + 1
    selection = f"{yr}-05-01"
    
    eps_data = {}
    for _, r in all_fin[all_fin.year == year].iterrows():
        if pd.notna(r.BASIC_EPS) and r.BASIC_EPS > 0:
            eps_data[r.code] = r.BASIC_EPS
    
    pe_vals = []
    for code, eps in eps_data.items():
        px = get_price_near(code, selection)
        if px and px > 0:
            pe = px / eps
            if 0 < pe < 500:  # filter extreme PEs
                pe_vals.append((code, pe))
    
    if pe_vals:
        all_pes = [x[1] for x in pe_vals]
        for code, pe in pe_vals:
            # Percentile rank: lower PE = lower percentile = higher score
            pct = sum(1 for c, p in pe_vals if p < pe) / len(pe_vals)
            # Inverse: cheap companies (low PE pct) get HIGH score
            val_score = 1 - pct  # 0 (expensive) to 1 (cheap)
            val_by_year[(code, yr)] = val_score

print(f"Valuation data: {len(val_by_year)} (code, year) pairs")

# --- Step 5: Load existing V2 selections and compute composite score ---
sel_path = HERE / "data" / "selections_full_v2_prices.csv"
rows = []
with open(sel_path) as f:
    reader = csv.DictReader(f)
    for r in reader:
        rows.append(r)

print(f"\nV2 selections: {len(rows)} rows, {len(set(r['code'] for r in rows))} companies")

# For each selection row, compute composite score
composite_rows = []
for r in rows:
    code = r["code"]
    year = int(r["signal_date"][:4])
    old_score = float(r["score"]) if r["score"] else 0.5
    
    # Get normalized scores
    ind = ind_of.get(code, "?")
    t = track_scores.get(ind, 0)
    tl = toll_scores.get(code, 0)
    v = val_by_year.get((code, year), 0.5)  # default to neutral
    q = old_score  # financial quality score
    
    # Normalize track and toll to [0, 1] range
    t_norm = min(max(t / 20, 0), 1) if t > 0 else 0
    tl_norm = min(max(tl / 30, 0), 1)
    
    # Composite: track(30%) + toll(40%) + valuation(20%) + quality(10%)
    composite = 0.30 * t_norm + 0.40 * tl_norm + 0.20 * v + 0.10 * q
    
    composite_rows.append({**r, "composite_score": round(composite, 4), "t_norm": round(t_norm, 3), "tl_norm": round(tl_norm, 3), "v_score": round(v, 3)})

# Write new selections (replace score with composite)
fieldnames = [
    "code", "name", "notice_date", "update_date", "report_date",
    "revenue_yoy", "profit_yoy", "roe", "gross_margin", "ocf_per_share",
    "industry", "revision_version_verified", "plain_code", "code_name",
    "pct_revenue_yoy", "pct_profit_yoy", "pct_roe", "pct_gross_margin",
    "pct_ocf_per_share", "score", "report", "signal_date", "top_n",
]

out_path = HERE / "data" / "selections_composite.csv"
with open(out_path, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    for r in composite_rows:
        row = {k: r.get(k, "") for k in fieldnames}
        row["score"] = r["composite_score"]
        w.writerow(row)

print(f"Written: {out_path} ({len(composite_rows)} rows)")
codes = set(r["code"] for r in composite_rows)
print(f"Unique companies: {len(codes)}")

# Show score composition for top 10
print(f"\n=== Score composition (sample) ===")
scored = [(r["composite_score"], r) for r in composite_rows]
scored.sort(reverse=True)
for s, r in scored[:10]:
    ind = ind_of.get(r["code"], "?")
    print(f"  {r['code']} ({ind:20s}): composite {s:.3f} | track {r['t_norm']:.2f} toll {r['tl_norm']:.2f} val {r['v_score']:.2f} quality {float(r['score'] or 0.5):.2f}")

# Stats
avg_scores = {"track": 0, "toll": 0, "val": 0, "quality": 0}
for r in composite_rows:
    avg_scores["track"] += float(r.get("t_norm", 0))
    avg_scores["toll"] += float(r.get("tl_norm", 0))
    avg_scores["val"] += float(r.get("v_score", 0.5))
    avg_scores["quality"] += float(r.get("score", 0.5))
n = len(composite_rows)
for k in avg_scores:
    avg_scores[k] = round(avg_scores[k]/n, 3)
print(f"\nAverage component scores: {avg_scores}")

json.dump({"composite_score_weights": {"track":0.30,"toll":0.40,"valuation":0.20,"quality":0.10},"avg_scores": avg_scores}, 
          open(HERE/"data"/"composite-score-summary.json","w"))
