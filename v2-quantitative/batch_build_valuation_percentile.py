#!/usr/bin/env python3
"""
Build PIT valuation percentiles and fair value estimates for the 45 hot companies.
For each price row, compute:
  - PE = close / eps_pit
  - PB = close / bps_pit (if bps_pit > 0)
  - pe_percentile_5y: trailing 5-year percentile of PE ratio (ex-ante, i.e., only using
    data available BEFORE the current date)
  - pb_percentile_5y: same for PB
  - fair_value: estimated using PB-based model (fair PB = 20th percentile of historical PB)
    When current PB <= fair PB → undervalued; PE percentile >= 80% → overvalued exit signal
"""
from __future__ import annotations

import csv
import json
import math
import statistics
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
PANEL = DATA / "pit-valuation-panel.csv"


def running_percentile(window: list[float], value: float) -> float:
    """Percentile rank of `value` within `window` (0-1). """
    if not window:
        return 0.5
    count_below = sum(1 for v in window if v < value)
    count_equal = sum(1 for v in window if v == value)
    return (count_below + 0.5 * count_equal) / len(window)


def main():
    if not PANEL.exists():
        print(f"Panel not found: {PANEL}")
        return

    rows = []
    with open(PANEL) as f:
        for row in csv.DictReader(f):
            rows.append(row)

    # Group by company
    by_code = defaultdict(list)
    for row in rows:
        by_code[row["code"]].append(row)

    output = []
    hist_pe_5y: dict[str, deque] = {}  # code -> deque of historical PE ratios
    hist_pb_5y: dict[str, deque] = {}
    window_trading_days = 252 * 5  # ~5 years of trading days

    for code in sorted(by_code.keys()):
        company_rows = sorted(by_code[code], key=lambda x: x["date"])
        hist_pe = deque(maxlen=window_trading_days * 2)  # keep more to be safe
        hist_pb = deque(maxlen=window_trading_days * 2)

        for row in company_rows:
            date = row["date"]
            close_str = row.get("close", "")
            eps_str = row.get("eps_pit", "")
            bps_str = row.get("bps_pit", "")

            try:
                close = float(close_str) if close_str else None
                eps = float(eps_str) if eps_str else None
                bps = float(bps_str) if bps_str else None
            except (ValueError, TypeError):
                continue

            if close is None or close <= 0:
                continue

            pe = (close / eps) if eps and eps > 0 else None
            pb = (close / bps) if bps and bps > 0 else None

            # Percentiles: ex-ante (using ONLY data before this date)
            pe_pct = running_percentile(list(hist_pe), pe) if pe is not None and hist_pe else None
            pb_pct = running_percentile(list(hist_pb), pb) if pb is not None and hist_pb else None

            # Fair value estimate: fair PB = 20th percentile of historical PB
            fair_pb = None
            fair_value = None
            if hist_pb and pb is not None:
                sorted_pb = sorted(hist_pb)
                fair_pb = sorted_pb[max(0, int(len(sorted_pb) * 0.2) - 1)]
                if bps and bps > 0:
                    fair_value = fair_pb * bps

            output.append({
                "code": code,
                "date": date,
                "close": close,
                "eps_pit": eps,
                "bps_pit": bps,
                "pe": pe,
                "pb": pb,
                "pe_percentile_5y": round(pe_pct, 4) if pe_pct is not None else None,
                "pb_percentile_5y": round(pb_pct, 4) if pb_pct is not None else None,
                "fair_value": round(fair_value, 4) if fair_value is not None else None,
                "upside_to_fair": round((fair_value - close) / close, 4) if fair_value and close else None,
            })

            # Add current values to historical window (for FUTURE rows)
            if pe is not None and pe > 0 and pe < 500:
                hist_pe.append(pe)
            if pb is not None and pb > 0 and pb < 50:
                hist_pb.append(pb)

    # Write output
    vf = DATA / "pit-valuation-percentile.csv"
    if output:
        with open(vf, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=output[0].keys())
            w.writeheader()
            w.writerows(output)
        print(f"Written: {vf} ({len(output)} rows)")

    # Stats
    with_pe = sum(1 for r in output if r["pe_percentile_5y"] is not None)
    with_fv = sum(1 for r in output if r["fair_value"] is not None)
    companies = len(set(r["code"] for r in output))
    print(f"  Companies: {companies}")
    print(f"  Rows with PE percentile: {with_pe}")
    print(f"  Rows with fair value: {with_fv}")

    # Entry/exit signal counts
    exit_signals = sum(1 for r in output if r["pe_percentile_5y"] is not None and r["pe_percentile_5y"] >= 0.8)
    entry_candidates = sum(1 for r in output if r["fair_value"] is not None and r.get("upside_to_fair", 0) > 0)
    print(f"  Exit signal days (PE >= 80%): {exit_signals}")
    print(f"  Entry candidate days (below fair value): {entry_candidates}")

    summary = {
        "rows": len(output), "companies": companies,
        "with_pe_percentile": with_pe, "with_fair_value": with_fv,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (DATA / "pit-valuation-percentile-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
