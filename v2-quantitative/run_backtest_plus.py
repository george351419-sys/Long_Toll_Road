#!/usr/bin/env python3
"""
Enhanced backtest with momentum stop-loss and concentration.
Adds to the original engine:
  - Stop-loss: sell if position drops 5%/10%/15% from purchase high
  - Works with any top_n (no longer requires matching top_n column)
"""
from __future__ import annotations

import csv, json, math, statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "output"
PIT_DB = HERE.parent / "industry_pit/complete-system-v1" / "data" / "pit-facts.sqlite"

@dataclass
class Cost:
    commission: float; minimum: float; slippage: float; stamp: float

def load_bars(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype={"code": str, "tradestatus": str, "isST": str})
    frame["date"] = pd.to_datetime(frame["date"])
    for c in ("open", "high", "low", "close", "volume", "pctChg"):
        frame[c] = pd.to_numeric(frame[c], errors="coerce")
    return frame.set_index("date").sort_index()

def metric(nav: pd.Series) -> dict:
    nav = nav.dropna()
    if len(nav) < 2: return {"cagr": 0, "max_drawdown": 0, "sharpe": 0, "calmar": None}
    years = (nav.index[-1] - nav.index[0]).days / 365.25
    returns = nav.pct_change().dropna()
    peak = nav.cummax()
    dd = nav / peak - 1
    cagr = (nav.iloc[-1] / nav.iloc[0]) ** (1 / years) - 1
    vol = returns.std() * math.sqrt(252)
    sharpe = returns.mean() / returns.std() * math.sqrt(252) if returns.std() else 0
    mdd = dd.min()
    return {"start": str(nav.index[0].date()), "end": str(nav.index[-1].date()),
            "cagr": cagr, "volatility": vol, "sharpe": sharpe,
            "max_drawdown": mdd, "calmar": cagr / abs(mdd) if mdd and abs(mdd) > 0.001 else None}

def can_trade(row, side: str) -> bool:
    if row is None or row["tradestatus"] != "1" or row["volume"] <= 0: return False
    one_price = abs(row["high"] - row["low"]) < 1e-9
    if side == "buy" and one_price and row["pctChg"] >= 4.8: return False
    if side == "sell" and one_price and row["pctChg"] <= -4.8: return False
    return True

def simulate(calendar, bars, schedules, initial: float, cost: Cost, stop_loss: float = 0.0, peak_prices: dict = None):
    """Extended simulate with optional stop-loss tracking."""
    cash, shares, pending = initial, {}, {}
    nav_rows, events, turnover, blocked = [], [], 0.0, 0
    peaks = peak_prices or {}
    schedule_map = {d: targets for d, targets in schedules}
    last_close = {}

    for current in calendar:
        for code, frame in bars.items():
            if current in frame.index and pd.notna(frame.at[current, "close"]):
                px = frame.at[current, "close"]
                last_close[code] = px
                # Track peak price for stop-loss
                if code not in peaks or px > peaks[code]:
                    peaks[code] = px

        # Rebalance
        if current in schedule_map:
            targets = schedule_map[current]
            value = cash + sum(q * last_close.get(c, 0) for c, q in shares.items())
            desired = {}
            for code, weight in targets.items():
                row = bars[code].loc[current] if current in bars[code].index else None
                ref = row["open"] if row is not None and pd.notna(row["open"]) else last_close.get(code)
                if ref:
                    desired[code] = math.floor(value * weight / ref / 100) * 100
            pending = {c: q - shares.get(c, 0) for c, q in desired.items()}
            for code, qty in shares.items():
                if code not in desired and qty:
                    pending[code] = -qty
            # Reset peaks on rebalance
            for code in desired:
                if code in peaks: peaks.pop(code, None)

        # Stop-loss check: add sell orders for positions exceeding drawdown threshold
        if stop_loss > 0:
            for code, qty in list(shares.items()):
                if qty == 0: continue
                px = last_close.get(code)
                peak = peaks.get(code)
                if px and peak and peak > 0:
                    dd = px / peak - 1
                    if dd < -stop_loss:
                        if code not in pending or pending[code] >= 0:
                            pending[code] = -qty
                            events.append({"date": str(current.date()), "code": code,
                                            "side": "stop_loss", "shares": qty,
                                            "peak": peak, "current": px,
                                            "drawdown": round(dd, 4)})

        # Execute sells then buys
        for side in ("sell", "buy"):
            for code in sorted(list(pending)):
                qty = pending[code]
                if (side == "sell" and qty >= 0) or (side == "buy" and qty <= 0): continue
                frame = bars.get(code)
                row = frame.loc[current] if frame is not None and current in frame.index else None
                if not can_trade(row, side):
                    blocked += 1; continue
                slip = cost.slippage / 10000
                fill = row["open"] * (1 - slip if side == "sell" else 1 + slip)
                trade_qty = abs(qty)
                if side == "buy":
                    affordable = math.floor(cash / (fill * (1 + cost.commission)) / 100) * 100
                    trade_qty = min(trade_qty, affordable)
                    if trade_qty <= 0: continue
                gross = trade_qty * fill
                fee = max(cost.minimum, gross * cost.commission) + (gross * cost.stamp if side == "sell" else 0)
                if side == "buy":
                    cash -= gross + fee; shares[code] = shares.get(code, 0) + trade_qty
                else:
                    cash += gross - fee; shares[code] = shares.get(code, 0) - trade_qty
                pending[code] -= trade_qty if side == "buy" else -trade_qty
                turnover += gross
                if pending.get(code) == 0: pending.pop(code, None)
                if shares.get(code) == 0: shares.pop(code, None)

        value = cash + sum(q * last_close.get(c, 0) for c, q in shares.items())
        nav_rows.append((current, value / initial))

    return pd.Series(dict(nav_rows)).sort_index(), events, turnover / initial, blocked

def build_schedules(selections, calendar, top_n: int, delay: int):
    """Build rebalance schedules from selections, picking top_n by score."""
    result = []
    for signal, group in selections.groupby("signal_date"):
        later = calendar[calendar > pd.Timestamp(signal)]
        if len(later) <= delay: continue
        execution = later[delay]
        group = group.sort_values("score", ascending=False).head(top_n)
        weights = pd.Series(1 / len(group), index=group.index)
        result.append((execution, dict(zip(group["code"], weights))))
    return result

def main():
    plan = json.loads((HERE / "experiment-plan.json").read_text())
    selections = pd.read_csv(DATA / "selections.csv", dtype={"code": str})
    selections["code"] = selections.code.str.zfill(6)
    selections["signal_date"] = selections.signal_date.astype(str)
    codes = sorted(selections.code.unique())

    bars = {}
    for code in codes:
        p = DATA / "prices" / f"{code}.csv"
        if p.exists():
            bars[code] = load_bars(p)
    print(f"Loaded {len(bars)}/{len(codes)} price files")

    csi800 = load_bars(DATA / "prices" / "CSI800.csv")
    calendar = csi800.index[(csi800.index >= "2016-05-01") & (csi800.index <= "2024-12-31")]

    base_cost = Cost(0.00025, 5, 5/10000, 0.001)

    # Test variants: concentration + stop-loss
    variants = [
        ("top20", 20, 0.00),    # baseline
        ("top15", 15, 0.00),    # more concentrated
        ("top10", 10, 0.00),    # concentrated
        ("top15_sl10", 15, 0.10),  # concentrated + 10% stop-loss
        ("top10_sl10", 10, 0.10),  # concentrated + 10% stop-loss
        ("top15_sl15", 15, 0.15),  # concentrated + 15% stop-loss
        ("top10_sl15", 10, 0.15),  # concentrated + 15% stop-loss
    ]

    navs, summaries, all_events = {}, {}, []
    for key, top_n, sl in variants:
        schedules = build_schedules(selections, calendar, top_n, 0)
        nav, events, turnover, blocked = simulate(calendar, bars, schedules, 10_000_000, base_cost, stop_loss=sl)
        navs[key] = nav
        summaries[key] = {**metric(nav), "turnover_multiple": turnover, "trade_count": len(events), "blocked_order_days": blocked, "stop_loss": sl, "top_n": top_n}

    # Benchmark
    bm = csi800.loc[calendar, "close"].dropna()
    bm = bm / bm.iloc[0]
    bm_metrics = metric(bm)

    print(f"\nCSI800: CAGR {bm_metrics['cagr']*100:.2f}% | MDD {bm_metrics['max_drawdown']*100:.2f}% | Sharpe {bm_metrics['sharpe']:.2f}")
    print(f"\n{'Variant':20s} {'CAGR':>8s} {'Excess':>8s} {'MDD':>8s} {'Sharpe':>6s} {'TO':>6s} {'Calmar':>8s}")
    print("-" * 70)
    for key in [v[0] for v in variants]:
        s = summaries[key]
        excess = s["cagr"] - bm_metrics["cagr"]
        calmar_s = f"{s['calmar']:.2f}" if s.get('calmar') else "N/A"
        print(f"{key:20s} {s['cagr']*100:>7.2f}% {excess*100:>7.2f}% {s['max_drawdown']*100:>7.2f}% {s['sharpe']:>5.2f} {s['turnover_multiple']:>5.1f}x {calmar_s:>8s}")

    # Save results
    OUT.mkdir(parents=True, exist_ok=True)
    result = {"variants": summaries, "benchmark": bm_metrics, "generated_at": datetime.now().isoformat()}
    (OUT / "summary_enhanced.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\nSaved: {OUT / 'summary_enhanced.json'}")

if __name__ == "__main__":
    main()
