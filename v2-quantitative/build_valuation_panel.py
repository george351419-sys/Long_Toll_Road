#!/usr/bin/env python3
"""Build a no-lookahead daily valuation candidate panel from unadjusted prices."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
WINDOW = 1210


def compute_ttm(group: pd.DataFrame) -> pd.DataFrame:
    group = group.sort_values("report_date").copy()
    eps_by_report = dict(zip(group["report"], group["eps"]))
    ttm, normalized = [], []
    annual_positive = []
    for row in group.itertuples():
        report = str(row.report)
        year, mmdd = int(report[:4]), report[-4:]
        if mmdd == "1231":
            value = row.eps
            if pd.notna(row.eps) and row.eps > 0:
                annual_positive.append(float(row.eps))
        else:
            current = row.eps
            prior_annual = eps_by_report.get(f"{year - 1}1231")
            prior_same = eps_by_report.get(f"{year - 1}{mmdd}")
            value = (
                current + prior_annual - prior_same
                if all(pd.notna(item) for item in (current, prior_annual, prior_same))
                else np.nan
            )
        ttm.append(value)
        history = annual_positive[-3:]
        normalized.append(
            min(float(value), float(np.median(history)))
            if pd.notna(value) and value > 0 and history
            else np.nan
        )
    group["ttm_eps"] = ttm
    group["normalized_eps"] = normalized
    return group


def percentile_last(values) -> float:
    series = pd.Series(values)
    if series.iloc[-1] <= 0:
        return np.nan
    return float(series.rank(pct=True, method="average").iloc[-1])


def main() -> None:
    financial = pd.read_csv(
        HERE / "data" / "quarterly-source-linked.csv",
        dtype={"code": str, "report": str},
    )
    financial["code"] = financial["code"].str.zfill(6)
    financial["report_date"] = pd.to_datetime(financial["report_date"], errors="coerce")
    financial["available_date"] = pd.to_datetime(
        financial["source_published_at"], errors="coerce", utc=True
    ).dt.tz_convert("Asia/Shanghai").dt.tz_localize(None).dt.normalize()
    fallback = pd.to_datetime(financial["available_after"], errors="coerce", utc=True)
    fallback = fallback.dt.tz_convert("Asia/Shanghai").dt.tz_localize(None).dt.normalize()
    financial["available_date"] = financial["available_date"].fillna(fallback)
    financial = pd.concat(
        [compute_ttm(group) for _, group in financial.groupby("code")],
        ignore_index=True,
    )
    panels = []
    for code, updates in financial.groupby("code"):
        path = HERE / "data" / "valuation-prices" / f"{code}.csv.gz"
        prices = pd.read_csv(path, dtype={"code": str, "isST": str, "tradestatus": str})
        prices["date"] = pd.to_datetime(prices["date"])
        prices["close"] = pd.to_numeric(prices["close"], errors="coerce")
        updates = updates.sort_values("available_date")
        keep = [
            "available_date",
            "report",
            "industry",
            "ttm_eps",
            "normalized_eps",
            "bps",
            "strict_source_link",
            "source_id",
            "source_document_id",
            "source_pdf_sha256",
        ]
        merged = pd.merge_asof(
            prices.sort_values("date"),
            updates[keep].sort_values("available_date"),
            left_on="date",
            right_on="available_date",
            direction="backward",
        )
        merged["plain_code"] = code
        merged["pe_ttm"] = merged["close"] / merged["ttm_eps"].where(merged["ttm_eps"] > 0)
        merged["pb"] = merged["close"] / merged["bps"].where(merged["bps"] > 0)
        valid_pe = merged["pe_ttm"].where((merged["pe_ttm"] > 0) & (merged["pe_ttm"] < 1000))
        merged["pe_median_5y"] = valid_pe.rolling(WINDOW, min_periods=WINDOW).median()
        merged["pe_percentile_5y"] = valid_pe.rolling(
            WINDOW, min_periods=WINDOW
        ).apply(percentile_last, raw=False)
        merged["valuation_history_days"] = valid_pe.notna().rolling(WINDOW).sum()
        panels.append(merged)
    panel = pd.concat(panels, ignore_index=True)
    panel["industry_pe_median"] = panel.groupby(["date", "industry"])["pe_ttm"].transform(
        lambda values: values.where((values > 0) & (values < 1000)).median()
    )
    panel["fair_multiple"] = panel[["pe_median_5y", "industry_pe_median"]].min(axis=1)
    panel["fair_multiple"] = panel["fair_multiple"].clip(upper=15)
    panel["fair_value"] = panel["normalized_eps"] * panel["fair_multiple"]
    panel["valuation_candidate_ready"] = (
        panel["strict_source_link"].fillna(False)
        & panel["valuation_history_days"].ge(WINDOW)
        & panel["ttm_eps"].gt(0)
        & panel["bps"].gt(0)
        & panel["fair_value"].gt(0)
    )
    panel["entry_margin"] = (
        panel["valuation_candidate_ready"]
        & panel["pe_percentile_5y"].le(0.20)
        & panel["close"].le(0.8 * panel["fair_value"])
    )
    panel["valuation_exit_base"] = (
        panel["valuation_candidate_ready"]
        & (
            panel["pe_percentile_5y"].ge(0.80)
            | panel["close"].ge(1.2 * panel["fair_value"])
        )
    )
    columns = [
        "date",
        "plain_code",
        "close",
        "tradestatus",
        "isST",
        "industry",
        "report",
        "available_date",
        "ttm_eps",
        "normalized_eps",
        "bps",
        "pe_ttm",
        "pb",
        "pe_median_5y",
        "pe_percentile_5y",
        "industry_pe_median",
        "fair_multiple",
        "fair_value",
        "valuation_history_days",
        "valuation_candidate_ready",
        "entry_margin",
        "valuation_exit_base",
        "source_id",
        "source_document_id",
        "source_pdf_sha256",
    ]
    output = HERE / "data" / "valuation-panel.csv.gz"
    panel[columns].to_csv(output, index=False, compression="gzip")
    summary = {
        "rows": len(panel),
        "companies": int(panel["plain_code"].nunique()),
        "ready_rows": int(panel["valuation_candidate_ready"].sum()),
        "entry_rows": int(panel["entry_margin"].sum()),
        "exit_rows": int(panel["valuation_exit_base"].sum()),
        "first_ready_date": (
            panel.loc[panel["valuation_candidate_ready"], "date"].min().date().isoformat()
            if panel["valuation_candidate_ready"].any()
            else None
        ),
        "price_basis": "unadjusted",
        "history_window_trading_days": WINDOW,
        "status": "candidate_only_not_verified_until_numeric_pdf_reconciliation",
    }
    (HERE / "data" / "valuation-coverage.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
