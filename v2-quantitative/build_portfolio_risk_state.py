#!/usr/bin/env python3
"""Build lagged CSI800 volatility exposure states for next-session execution."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
PIT = HERE.parent.parent / "pit"


def main() -> None:
    benchmark = pd.read_csv(PIT / "data" / "prices" / "CSI800.csv")
    benchmark["date"] = pd.to_datetime(benchmark["date"])
    benchmark["close"] = pd.to_numeric(benchmark["close"], errors="coerce")
    benchmark = benchmark.sort_values("date")
    benchmark["return"] = benchmark["close"].pct_change()
    benchmark["realized_vol_60"] = benchmark["return"].rolling(60, min_periods=60).std() * np.sqrt(252)
    benchmark["vol_target_exposure_signal"] = (
        0.15 / benchmark["realized_vol_60"]
    ).clip(lower=0.30, upper=1.0)
    benchmark["exposure_change"] = benchmark["vol_target_exposure_signal"].diff().abs()
    benchmark["rebalance_required"] = benchmark["exposure_change"].ge(0.10)
    benchmark["effective_next_session_exposure"] = benchmark[
        "vol_target_exposure_signal"
    ].shift(1)
    benchmark["signal_at"] = benchmark["date"].dt.strftime("%Y-%m-%d") + "T15:00:00+08:00"
    benchmark["status"] = "verified_from_local_benchmark_close"
    columns = [
        "date",
        "close",
        "return",
        "realized_vol_60",
        "vol_target_exposure_signal",
        "rebalance_required",
        "effective_next_session_exposure",
        "signal_at",
        "status",
    ]
    output = HERE / "data"
    output.mkdir(parents=True, exist_ok=True)
    benchmark[columns].to_csv(output / "portfolio-risk-state.csv", index=False)
    summary = {
        "rows": len(benchmark),
        "first_signal": (
            benchmark.loc[benchmark["realized_vol_60"].notna(), "date"].min().date().isoformat()
        ),
        "rebalance_signals": int(benchmark["rebalance_required"].sum()),
        "mean_target_exposure": float(benchmark["vol_target_exposure_signal"].mean()),
        "days_at_floor": int(benchmark["vol_target_exposure_signal"].eq(0.30).sum()),
        "lookback_days": 60,
        "annualized_target": 0.15,
        "execution_lag_sessions": 1,
    }
    (output / "portfolio-risk-coverage.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
