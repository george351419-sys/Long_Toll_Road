#!/usr/bin/env python3
"""
Build V1 (track gate) and V2 (track+toll gate) selections using
approximate industry and company-level proxies from our PIT data.

Track gate: filters industries by aggregate revenue growth + margin stability
Toll gate: filters companies by gross margin level/stability + ROE + cash flow quality

Generates three selection files:
  selections_v0.csv - all companies (baseline)
  selections_v1.csv - track-gate filtered only
  selections_v2.csv - track + toll gates
"""
from __future__ import annotations

import csv, json, sqlite3, statistics
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
PIT_DB = HERE.parent / "industry_pit/complete-system-v1" / "data" / "pit-facts.sqlite"
SELECTIONS_DIR = HERE / "data"

HOT_CODES = [
    "000036","000048","000636","000663","000791","000799","000810",
    "000968","000975","001203","002032","002098","002128","002258",
    "002304","002432","002460","002466","002468","002508","002558",
    "002677","002739","002847","002963","300002","300080","300196",
    "300246","300343","300410","300450","300461","300531","300586",
    "300592","300618","300693","300735","300770","300785","300856",
    "300896","300979","301004",
]

# Load industry labels
with open(HERE.parent / "industry_pit" / "cohort" / "cohort-v1.csv") as f:
    reader = csv.DictReader(f)
    ind_of = {r["code"]: r.get("industry_at_last_seen", "") for r in reader}


def load_pit_data():
    conn = sqlite3.connect(PIT_DB)
    rows = conn.execute(
        """SELECT entity_id, effective_at, value_json
           FROM facts WHERE entity_type='governance'
           AND metric_id='quarterly_fundamentals'
           AND verification_status='verified'
           ORDER BY entity_id, effective_at"""
    ).fetchall()
    conn.close()

    data = defaultdict(lambda: defaultdict(dict))
    for code, effective, value_str in rows:
        if code not in HOT_CODES:
            continue
        year = effective[:4]
        try:
            metrics = json.loads(value_str).get("metrics", {})
        except (json.JSONDecodeError, TypeError):
            continue
        data[code][year] = metrics
    return dict(data)


def compute_industry_track_scores(all_data: dict) -> dict[str, dict]:
    """Compute track scores for each industry based on aggregate financial data."""
    by_industry = defaultdict(lambda: defaultdict(list))

    for code, years in all_data.items():
        ind = ind_of.get(code, "未知")
        for year, m in years.items():
            if m.get("revenue_yoy") is not None:
                by_industry[ind][year].append(m["revenue_yoy"])
            if m.get("gross_margin") is not None:
                by_industry[ind]["gm"].append(m["gross_margin"])

    scores = {}
    for ind, years_data in by_industry.items():
        all_rev = []
        for year in sorted(years_data.keys()):
            if year == "gm":
                continue
            all_rev.extend(years_data[year])
        margins = years_data.get("gm", [])

        if len(all_rev) < 5 or not margins:
            continue

        med_rev = statistics.median(all_rev)
        med_gm = statistics.median(margins) if margins else 0
        # Score: higher med_rev growth + higher margins + lower volatility
        rev_std = statistics.stdev(all_rev) if len(all_rev) > 1 else float("inf")
        gm_std = statistics.stdev(margins) if len(margins) > 1 else float("inf")

        # Growth/momentum adjusted for volatility
        growth_score = max(med_rev, -50) / max(rev_std, 0.1)
        margin_score = max(med_gm, 0) / max(gm_std, 0.1)
        track_score = growth_score * (margin_score / 5)

        companies_in_ind = [c for c in HOT_CODES if ind_of.get(c) == ind]
        scores[ind] = {
            "track_score": track_score,
            "companies": companies_in_ind,
            "n_companies": len(companies_in_ind),
            "med_rev_growth": med_rev,
            "med_margin": med_gm,
        }
    return scores


def compute_toll_scores(all_data: dict) -> dict[str, float]:
    """Compute toll scores for each company based on financial moat proxies."""
    toll_scores = {}

    for code, years in all_data.items():
        gm_vals = []
        roe_vals = []
        rev_vals = []
        ocf_vals = []

        for year, m in years.items():
            if m.get("gross_margin") is not None:
                gm_vals.append(m["gross_margin"])
            if m.get("roe") is not None:
                roe_vals.append(m["roe"])
            if m.get("revenue_yoy") is not None:
                rev_vals.append(m["revenue_yoy"])

        if len(gm_vals) < 3:
            toll_scores[code] = 0
            continue

        avg_gm = statistics.mean(gm_vals)
        gm_stability = statistics.stdev(gm_vals) if len(gm_vals) > 1 else 999

        avg_roe = statistics.mean(roe_vals) if roe_vals else 0

        # Toll score components:
        # 1. High margin level (>40% is good toll characteristic)
        # 2. Margin stability (low std dev = pricing power)
        # 3. High ROE (>15% = capital efficiency)
        margin_score = max(avg_gm, 0) / 40.0
        stability_score = max(min(50 / max(gm_stability, 0.1), 10), 0.5)
        roe_score = max(min(avg_roe / 15.0, 3), 0)

        toll_scores[code] = margin_score * stability_score * roe_score

    return toll_scores


def build_selections(all_data: dict, track_scores: dict, toll_scores: dict):
    """Build three sets of selections."""
    all_years = set()
    for code, years in all_data.items():
        for year_str in years:
            if year_str.isdigit():
                all_years.add(int(year_str))

    # For each year, compute company scores and filter
    all_selections = []
    v1_selections = []
    v2_selections = []

    for year in sorted(all_years):
        signal_date = f"{year+1}-05-01"
        if year + 1 > 2024:
            continue

        # Collect all companies with annual data for this year
        company_scores = []
        for code in HOT_CODES:
            ystr = str(year)
            if code not in all_data or ystr not in all_data[code]:
                continue
            metrics = all_data[code][ystr]
            rev = metrics.get("revenue_yoy")
            profit = metrics.get("profit_yoy")
            roe = metrics.get("roe")
            gm = metrics.get("gross_margin")

            company_scores.append((code, metrics, rev, profit, roe, gm))

        if len(company_scores) < 5:
            continue

        # Percentile-based scoring
        def pct_of(vals, val):
            if not vals or val is None:
                return 0.5
            below = sum(1 for v in vals if v < val)
            equal = sum(1 for v in vals if v == val)
            return (below + 0.5 * equal) / len(vals)

        # Track gate: determine which industries pass
        passing_track_inds = set()
        for ind, info in track_scores.items():
            if info["track_score"] >= 0:  # non-negative = pass
                passing_track_inds.add(ind)

        # Compute who passes each gate
        v1_eligible = []
        v2_eligible = []

        for code, metrics, rev, profit, roe, gm in company_scores:
            ind = ind_of.get(code, "未知")
            track_pass = ind in passing_track_inds
            toll = toll_scores.get(code, 0)
            toll_pass = toll >= 1.5  # threshold tuned

            rev_vals = [c[2] for c in company_scores if c[2] is not None]
            profit_vals = [c[3] for c in company_scores if c[3] is not None]
            roe_vals = [c[4] for c in company_scores if c[4] is not None]
            gm_vals = [c[5] for c in company_scores if c[5] is not None]

            score = (
                pct_of(rev_vals, rev) + pct_of(profit_vals, profit)
                + pct_of(roe_vals, roe) + pct_of(gm_vals, gm)
            ) / 4.0

            row = {
                "code": code, "name": code, "notice_date": signal_date,
                "update_date": signal_date, "report_date": f"{year}1231",
                "revenue_yoy": rev or "", "profit_yoy": profit or "",
                "roe": roe or "", "gross_margin": gm or "",
                "ocf_per_share": metrics.get("ocf_per_share", ""),
                "industry": ind, "revision_version_verified": "False",
                "plain_code": code, "code_name": code,
                "pct_revenue_yoy": round(pct_of(rev_vals, rev), 4),
                "pct_profit_yoy": round(pct_of(profit_vals, profit), 4),
                "pct_roe": round(pct_of(roe_vals, roe), 4),
                "pct_gross_margin": round(pct_of(gm_vals, gm), 4),
                "pct_ocf_per_share": round(
                    pct_of([c[2] for c in company_scores if c[2] is not None],
                           metrics.get("ocf_per_share")), 4),
                "score": round(score, 4),
                "report": f"{year}1231", "signal_date": signal_date,
                "top_n": 20,
            }

            all_selections.append(row)
            if track_pass:
                v1_eligible.append((track_scores[ind]["track_score"], row))
            if track_pass and toll_pass:
                v2_eligible.append((toll_scores[code], row))

        # V1: select from track-passing companies
        v1_eligible.sort(key=lambda x: -x[0])
        for _, row in v1_eligible[:20]:
            v1_selections.append(row)

        # V2: select from track + toll passing companies
        v2_eligible.sort(key=lambda x: -x[0])
        for _, row in v2_eligible[:20]:
            v2_selections.append(row)

    return all_selections, v1_selections, v2_selections


def write_selections(filename: str, selections: list):
    path = SELECTIONS_DIR / filename
    fieldnames = [
        "code", "name", "notice_date", "update_date", "report_date",
        "revenue_yoy", "profit_yoy", "roe", "gross_margin", "ocf_per_share",
        "industry", "revision_version_verified", "plain_code", "code_name",
        "pct_revenue_yoy", "pct_profit_yoy", "pct_roe", "pct_gross_margin",
        "pct_ocf_per_share", "score", "report", "signal_date", "top_n",
    ]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for s in selections:
            w.writerow({k: s.get(k, "") for k in fieldnames})
    print(f"  Written: {path} ({len(selections)} rows)")


def main():
    print("Loading PIT quarterly data...")
    all_data = load_pit_data()
    print(f"  Companies loaded: {len(all_data)}")

    print("\nComputing industry track scores...")
    track_scores = compute_industry_track_scores(all_data)
    passing = [(ind, info) for ind, info in track_scores.items() if info["track_score"] >= 0]
    failing = [(ind, info) for ind, info in track_scores.items() if info["track_score"] < 0]
    print(f"  Track-passing industries: {len(passing)}")
    for ind, info in sorted(passing, key=lambda x: -x[1]["track_score"]):
        print(f"    ✅ {ind:20s} score {info['track_score']:.2f} ({info['n_companies']} companies)")
    print(f"  Track-failing industries: {len(failing)}")
    for ind, info in sorted(failing, key=lambda x: x[1]["track_score"]):
        print(f"    ❌ {ind:20s} score {info['track_score']:.2f} ({info['n_companies']} companies)")

    print("\nComputing company toll scores...")
    toll_scores = compute_toll_scores(all_data)
    top_toll = sorted(toll_scores.items(), key=lambda x: -x[1])[:10]
    bottom_toll = [x for x in sorted(toll_scores.items(), key=lambda x: x[1])[:5]]
    print(f"  Top 10 toll scores:")
    for code, score in top_toll:
        ind = ind_of.get(code, "")
        print(f"    {code} ({ind:15s}): {score:.2f}")
    print(f"  Bottom 5 toll scores:")
    for code, score in bottom_toll:
        ind = ind_of.get(code, "")
        print(f"    {code} ({ind:15s}): {score:.2f}")

    print("\nGenerating selections...")
    all_sel, v1_sel, v2_sel = build_selections(all_data, track_scores, toll_scores)

    # Copy V0 to selections.csv for the engine
    write_selections("selections.csv", all_sel)
    all_companies = set(s["code"] for s in all_sel)
    v1_companies = set(s["code"] for s in v1_sel)
    v2_companies = set(s["code"] for s in v2_sel)
    print(f"\n  V0 (all cos): {len(all_sel)} rows, {len(all_companies)} unique cos")
    print(f"  V1 (track):   {len(v1_sel)} rows, {len(v1_companies)} unique cos")
    print(f"  V2 (track+):  {len(v2_sel)} rows, {len(v2_companies)} unique cos")

    # Also write V1 and V2 as separate files for manual testing
    write_selections("selections_v1.csv", v1_sel)
    write_selections("selections_v2.csv", v2_sel)


if __name__ == "__main__":
    main()
