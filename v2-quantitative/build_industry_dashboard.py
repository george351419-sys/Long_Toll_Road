#!/usr/bin/env python3
"""Industry Financial Health Dashboard - from existing snapshot data."""
from __future__ import annotations

import csv, json, math, statistics
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
SNAPSHOTS = HERE.parent / ".." / "pit" / "data" / "financial_snapshots"
COHORT = HERE.parent / "cohort" / "cohort-v1.csv"

# Load industry mapping from cohort
industry_of = {}
with open(COHORT) as f:
    for r in csv.DictReader(f):
        code = r["code"].zfill(6)
        ind = r.get("industry_at_last_seen", "")
        if ind: industry_of[code] = ind
cohort_codes = set(industry_of.keys())
print(f"Loaded {len(cohort_codes)} cohort codes, {len(set(industry_of.values()))} industries")

# Load all snapshots, group by industry-year
ind_data = defaultdict(lambda: defaultdict(list))  # industry -> year -> list of metrics

for snap in sorted(SNAPSHOTS.glob("20*.csv")):
    with open(snap) as f:
        reader = csv.DictReader(f)
        for r in reader:
            code = r.get("SECURITY_CODE", "").zfill(6)
            if code not in cohort_codes:
                continue
            year = r.get("REPORTDATE", "")[:4]
            if not year.isdigit() or int(year) < 2014:
                continue
            ind = industry_of.get(code, "")
            if not ind:
                continue
            try:
                gm = float(r.get("XSMLL", "nan") or "nan")
                rev = float(r.get("YSTZ", "nan") or "nan")
                profit = float(r.get("SJLTZ", "nan") or "nan")
                roe = float(r.get("WEIGHTAVG_ROE", "nan") or "nan")
                eps = float(r.get("BASIC_EPS", "nan") or "nan")
                ocf = float(r.get("MGJYXJJE", "nan") or "nan")
            except (ValueError, TypeError):
                continue
            
            row = {"code": code, "gm": gm, "rev": rev, "profit": profit, "roe": roe, "eps": eps, "ocf": ocf}
            ind_data[ind][year].append(row)

print(f"Loaded data for {len(ind_data)} industries")

# Compute per-industry health scores
scores = {}
for ind, yd in sorted(ind_data.items()):
    # Collect all annual data points
    all_gm, all_rev, all_profit, all_roe = [], [], [], []
    ocf_quality = []
    year_count = 0
    
    for year in sorted(yd.keys()):
        yr_data = yd[year]
        gms = [d["gm"] for d in yr_data if not math.isnan(d["gm"])]
        revs = [d["rev"] for d in yr_data if not math.isnan(d["rev"])]
        profits = [d["profit"] for d in yr_data if not math.isnan(d["profit"])]
        roes = [d["roe"] for d in yr_data if not math.isnan(d["roe"])]
        epss = [d["eps"] for d in yr_data if not math.isnan(d["eps"])]
        ocfs = [d["ocf"] for d in yr_data if not math.isnan(d["ocf"])]
        
        if not gms or not revs: continue
        
        all_gm.extend(gms)
        all_rev.extend(revs)
        all_profit.extend(profits)
        all_roe.extend(roes)
        year_count += 1
        
        # OCF quality: if OCF < EPS * 0.8, earnings quality concern
        for eps_v, ocf_v in zip(epss, ocfs):
            if abs(eps_v) > 0.001:
                ocf_quality.append(ocf_v / eps_v)
    
    if year_count < 3 or len(all_gm) < 5:
        continue
    
    # 1. Gross margin score (higher = better, >40% = good)
    med_gm = statistics.median(all_gm)
    gm_score = min(med_gm / 40 if med_gm > 0 else 0, 2)
    gm_direction = all_gm[-1] - all_gm[0] if len(all_gm) >= year_count * 2 else 0
    
    # 2. Revenue growth stability (consistent > 10% growth = good)
    med_rev = statistics.median(all_rev)
    rev_stability = statistics.stdev(all_rev) if len(all_rev) > 2 else 999
    rev_score = min(max(med_rev, -20) / 15, 1.5) * (10 / max(rev_stability / 10, 1))
    
    # 3. Profit growth quality (positive profit growth = good)
    med_profit = statistics.median([p for p in all_profit if not math.isnan(p)])
    profit_score = min(med_profit / 20, 1.5) if med_profit > 0 else -0.5
    
    # 4. ROE score (>15% = good)
    med_roe = statistics.median([r for r in all_roe if not math.isnan(r)])
    roe_score = min(med_roe / 15, 2) if med_roe > 0 else 0
    
    # 5. Earnings quality
    med_ocf_ratio = statistics.median(ocf_quality) if ocf_quality else 0.5
    eq_score = min(med_ocf_ratio, 1.5) if med_ocf_ratio > 0.8 else max(med_ocf_ratio, 0)
    
    # Composite (max possible = 2+1.5+1.5+2+1.5 = 8.5, normalize to /10)
    raw = gm_score * 1.5 + rev_score + profit_score + roe_score * 1.5 + eq_score * 0.5
    total = min(raw / 8.5 * 10, 10)
    
    scores[ind] = {
        "composite_score": round(total, 2),
        "years": year_count,
        "companies_unique": len(set(d["code"] for yr_data in yd.values() for d in yr_data)),
        "med_gm": round(statistics.median(all_gm), 1) if all_gm else 0,
        "gm_trend_pp": round(gm_direction, 1),
        "med_rev_growth": round(statistics.median(all_rev), 1) if all_rev else 0,
        "rev_stability_cv": round(statistics.stdev(all_rev) / max(abs(statistics.median(all_rev)), 1), 2) if len(all_rev) > 2 else 0,
        "med_roe": round(statistics.median(all_roe), 1) if all_roe else 0,
        "med_profit_growth": round(statistics.median(all_profit), 1) if all_profit else 0,
        "ocf_quality_ratio": round(statistics.median(ocf_quality), 2) if ocf_quality else 0,
        "recent_gm": round(sum(all_gm[-3:])/3, 1) if len(all_gm) >= 3 else 0,
        "recent_rev": round(sum(all_rev[-3:])/3, 1) if len(all_rev) >= 3 else 0,
    }

# Write dashboard JSON
dashboard = {
    "generated_at": "2026-07-24",
    "total_industries": len(scores),
    "data_years": "2014-2024",
    "source": "financial snapshots (BaoStock vendor data)",
    "scoring_method": "Composite of gross margin(30%), revenue stability(20%), profit growth(10%), ROE(30%), earnings quality(10%)",
    "warning": "Scores are based on aggregate financial data from cohort companies. Industries with <3 companies may have inflated scores.",
}

# Rank by composite score
ranked = sorted(scores.items(), key=lambda x: -x[1]["composite_score"])
dashboard["top_5_industries"] = [{"industry": ind, "score": s["composite_score"],
    "gm": s["med_gm"], "rev": s["med_rev_growth"], "roe": s["med_roe"],
    "companies": s["companies_unique"], "trend": "improving" if s["gm_trend_pp"] > 0 else "declining"}
    for ind, s in ranked[:5]]
dashboard["bottom_5_industries"] = [{"industry": ind, "score": s["composite_score"],
    "gm": s["med_gm"], "rev": s["med_rev_growth"], "roe": s["med_roe"],
    "companies": s["companies_unique"], "trend": "improving" if s["gm_trend_pp"] > 0 else "declining"}
    for ind, s in ranked[-5:] if s["composite_score"] < 5]
dashboard["all_industries"] = {ind: s["composite_score"] for ind, s in scores.items()}

# Write files
out_json = DATA / "industry-health-dashboard.json"
with open(out_json, "w") as f:
    json.dump(dashboard, f, ensure_ascii=False, indent=2)

out_csv = DATA / "industry-health-data.csv"
with open(out_csv, "w", newline="") as f:
    keys = ["industry", "composite_score", "years", "companies_unique", "med_gm", "gm_trend_pp",
            "med_rev_growth", "rev_stability_cv", "med_roe", "med_profit_growth", "ocf_quality_ratio"]
    w = csv.DictWriter(f, fieldnames=keys)
    w.writeheader()
    for ind in sorted(scores.keys()):
        row = {"industry": ind}
        row.update({k: scores[ind].get(k, "") for k in keys[1:]})
        w.writerow(row)

print(f"\n=== Industry Health Dashboard ===")
print(f"Written: {out_json}")
print(f"Written: {out_csv}")
print(f"\n{'Industry':30s} {'Score':>6s} {'GM':>7s} {'RevG':>7s} {'ROE':>7s} {'Cos':>4s}")
print("-" * 65)
for ind, s in ranked[:10]:
    print(f"{ind:30s} {s['composite_score']:>5.1f} {s['med_gm']:>6.1f}% {s['med_rev_growth']:>6.1f}% {s['med_roe']:>6.1f}% {s['companies_unique']:>3d}")
print("...")
for ind, s in ranked[-5:]:
    if s['composite_score'] < 5:
        print(f"{ind:30s} {s['composite_score']:>5.1f} {s['med_gm']:>6.1f}% {s['med_rev_growth']:>6.1f}% {s['med_roe']:>6.1f}% {s['companies_unique']:>3d}")

