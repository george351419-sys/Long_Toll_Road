#!/usr/bin/env python3
"""Build PIT-aligned quarterly fundamental invalidation candidates for hot-archive companies."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
INDUSTRY = HERE.parent
PIT = INDUSTRY.parent / "pit"


def main() -> None:
    tiers = pd.read_csv(INDUSTRY / "archive" / "storage-tiers.csv", dtype={"code": str})
    hot_codes = set(tiers[tiers["storage_tier"].eq("hot_keep")]["code"].str.zfill(6))
    rows = []
    for path in sorted((PIT / "data" / "financial_snapshots").glob("20*.csv")):
        report = path.stem
        if report < "20150331" or report > "20240930":
            continue
        frame = pd.read_csv(path, dtype={"SECURITY_CODE": str})
        frame["code"] = frame["SECURITY_CODE"].str.zfill(6)
        frame = frame[frame["code"].isin(hot_codes)].copy()
        frame["available_after"] = pd.to_datetime(frame["NOTICE_DATE"], errors="coerce")
        frame["update_date"] = pd.to_datetime(frame["UPDATE_DATE"], errors="coerce")
        frame = frame.sort_values(["code", "update_date"]).drop_duplicates("code", keep="first")
        numeric = {
            "YSTZ": "revenue_yoy",
            "SJLTZ": "profit_yoy",
            "XSMLL": "gross_margin",
            "MGJYXJJE": "ocf_per_share",
            "BASIC_EPS": "eps",
            "BPS": "bps",
            "WEIGHTAVG_ROE": "roe",
        }
        for source, target in numeric.items():
            frame[target] = pd.to_numeric(frame[source], errors="coerce")
        frame["report"] = report
        frame["report_date"] = pd.to_datetime(frame["REPORTDATE"], errors="coerce")
        frame["ocf_to_eps_proxy"] = frame["ocf_per_share"] / frame["eps"].where(frame["eps"] != 0)
        frame["source_status"] = "candidate_only_not_verified"
        frame["strict_original_version"] = frame["update_date"] <= frame["available_after"]
        rows.append(
            frame[
                [
                    "code",
                    "SECURITY_NAME_ABBR",
                    "PUBLISHNAME",
                    "report",
                    "report_date",
                    "available_after",
                    "update_date",
                    "revenue_yoy",
                    "profit_yoy",
                    "gross_margin",
                    "ocf_per_share",
                    "eps",
                    "bps",
                    "ocf_to_eps_proxy",
                    "roe",
                    "source_status",
                    "strict_original_version",
                ]
            ].rename(
                columns={
                    "SECURITY_NAME_ABBR": "name",
                    "PUBLISHNAME": "industry",
                }
            )
        )
    panel = pd.concat(rows, ignore_index=True).sort_values(["code", "report_date"])
    panel["both_growth_negative"] = (panel["revenue_yoy"] < 0) & (panel["profit_yoy"] < 0)
    panel["cash_profit_divergence"] = (panel["ocf_per_share"] < 0) & (panel["eps"] > 0)
    panel["two_quarter_growth_invalidation"] = (
        panel.groupby("code")["both_growth_negative"].transform(
            lambda series: series & series.shift(1).fillna(False)
        )
    )
    panel["two_quarter_cash_invalidation"] = (
        panel.groupby("code")["cash_profit_divergence"].transform(
            lambda series: series & series.shift(1).fillna(False)
        )
    )
    prior_year_margin = panel.set_index(["code", "report_date"])["gross_margin"]
    margin_map = prior_year_margin.to_dict()
    panel["gross_margin_prior_year"] = [
        margin_map.get((row.code, row.report_date - pd.DateOffset(years=1)))
        for row in panel.itertuples()
    ]
    panel["gross_margin_yoy_drop_pp"] = panel["gross_margin_prior_year"] - panel["gross_margin"]
    panel["gross_margin_invalidation"] = panel["gross_margin_yoy_drop_pp"] >= 5
    panel["candidate_exit"] = (
        panel["two_quarter_growth_invalidation"]
        | panel["two_quarter_cash_invalidation"]
        | panel["gross_margin_invalidation"]
    )
    output = HERE / "data"
    output.mkdir(parents=True, exist_ok=True)
    panel.to_csv(output / "quarterly-fundamental-candidates.csv", index=False)
    coverage = {
        "companies": int(panel["code"].nunique()),
        "reports": int(panel["report"].nunique()),
        "rows": len(panel),
        "strict_original_version_rows": int(panel["strict_original_version"].sum()),
        "candidate_exit_rows": int(panel["candidate_exit"].sum()),
        "missing_fields": ["debt_ratio", "contract_liability_yoy", "original_quarterly_pdf"],
        "status": "candidate_only_not_verified",
        "warning": "Vendor snapshot values cannot prove original historical version; signals cannot enter strict PIT backtest until original quarterly filings are linked.",
    }
    (output / "quarterly-coverage.json").write_text(
        json.dumps(coverage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(coverage, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
