#!/usr/bin/env python3
"""
Test different holding periods for the Long Toll Road strategy.
The hypothesis: annual rebalancing cuts winners too short.
Longer holds should capture more of the multi-bagger returns.
"""
from __future__ import annotations

import json, math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "output"
SNAPSHOTS = HERE / "data" / "financial_snapshots"

@dataclass
class Cost:
    commission: float = 0.00025; minimum: float = 5; slippage: float = 5/10000; stamp: float = 0.001

def load_bars(path: Path) -> pd.DataFrame:
    try:
        frame = pd.read_csv(path, dtype={"code": str, "tradestatus": str, "isST": str})
        frame["date"] = pd.to_datetime(frame["date"])
        for c in ("open", "high", "low", "close", "volume", "pctChg"):
            frame[c] = pd.to_numeric(frame[c], errors="coerce")
        return frame.set_index("date").sort_index()
    except: return pd.DataFrame()

def metric(nav: pd.Series) -> dict:
    nav = nav.dropna()
    if len(nav) < 2: return {"cagr": 0, "max_drawdown": 0}
    years = (nav.index[-1] - nav.index[0]).days / 365.25
    returns = nav.pct_change().dropna()
    peak = nav.cummax(); dd = nav / peak - 1
    cagr = (nav.iloc[-1] / nav.iloc[0]) ** (1 / years) - 1
    vol = returns.std() * math.sqrt(252)
    sharpe = returns.mean() / returns.std() * math.sqrt(252) if returns.std() else 0
    mdd = dd.min()
    return {"cagr": cagr, "volatility": vol, "sharpe": sharpe,
            "max_drawdown": mdd, "calmar": cagr / abs(mdd) if mdd and abs(mdd) > 0.001 else None,
            "total_return": nav.iloc[-1] / nav.iloc[0] - 1}

def can_trade(row, side):
    if row is None or row["tradestatus"] != "1" or row["volume"] <= 0: return False
    one_price = abs(row["high"] - row["low"]) < 1e-9
    if side == "buy" and one_price and row["pctChg"] >= 4.8: return False
    if side == "sell" and one_price and row["pctChg"] <= -4.8: return False
    return True

# Load data
print("Loading data...")
csi800 = load_bars(DATA / "prices" / "CSI800.csv")
calendar = csi800.index[(csi800.index >= "2016-05-01") & (csi800.index <= "2024-12-31")]

selections = pd.read_csv(DATA / "selections.csv", dtype={"code": str})
selections["code"] = selections.code.str.zfill(6)
selections["signal_date"] = selections.signal_date.astype(str)

# Load all price bars
codes = set(selections.code.unique())
bars = {}
for code in codes:
    p = DATA / "prices" / f"{code}.csv"
    if p.exists():
        b = load_bars(p)
        if len(b) > 100: bars[code] = b

# Load quarterly data for invalidation
all_q = []
for snap in sorted(SNAPSHOTS.glob("20*.csv")):
    df = pd.read_csv(snap, dtype={"SECURITY_CODE": str, "NOTICE_DATE": str})
    for c in ["YSTZ","SJLTZ"]:
        if c in df.columns: df[c] = pd.to_numeric(df[c], errors="coerce")
    df["code"] = df.SECURITY_CODE.str.zfill(6)
    df["report_date"] = pd.to_datetime(df.REPORTDATE, errors="coerce")
    df["notice_date"] = pd.to_datetime(df.NOTICE_DATE, errors="coerce")
    all_q.append(df[["code","report_date","notice_date","YSTZ","SJLTZ"]])
all_q = pd.concat(all_q, ignore_index=True) if all_q else pd.DataFrame()
all_q = all_q.dropna(subset=["YSTZ","SJLTZ","notice_date"]).sort_values(["code","report_date"])

codes_with_prices = set()
for p in (DATA / "prices").glob("*.csv"):
    c = p.stem
    if c not in ("CSI800","CSI300"):
        codes_with_prices.add(c)

q_by_code = defaultdict(list)
for _, r in all_q.iterrows():
    q_by_code[r["code"]].append((r["report_date"], r["notice_date"], r["YSTZ"], r["SJLTZ"]))

# Filter to only companies with price data
q_by_code = {c: qs for c, qs in q_by_code.items() if c in codes_with_prices}
print(f"  q_by_code: {len(q_by_code)} companies (filtered to price codes)")
for c in q_by_code:
    q_by_code[c].sort(key=lambda x: x[0])

# Precompute quarterly invalidation first dates
qi_first = {}
for month in pd.date_range("2016-06-30", "2024-12-31", freq="ME"):
    for code, qs in q_by_code.items():
        recent = [q for q in qs if q[1] <= month]
        if len(recent) >= 2 and recent[-1][2] < 0 and recent[-1][3] < 0 and recent[-2][2] < 0 and recent[-2][3] < 0:
            if code not in qi_first:
                qi_first[code] = str(month.date())

print(f"Loaded: {len(bars)} bars, {len(qi_first)} qi_first")

COST = Cost()

def simulate(calendar, bars, schedules, initial, use_qi=True, use_sl=True, sl_pct=0.15):
    """Simulate with hold-to-thesis-break philosophy."""
    cash, shares = initial, {}
    peaks = {}
    nav_rows, sl_events, qi_events = [], [], []
    last_close = {}
    schedule_map = {d: targets for d, targets in schedules}
    
    # Track which positions were bought at which date (for holding period)
    position_entry = {}  # code -> date when first bought
    
    for current in calendar:
        # Update prices and peaks
        for code, frame in bars.items():
            if current in frame.index and pd.notna(frame.at[current, "close"]):
                px = frame.at[current, "close"]
                last_close[code] = px
                if code not in peaks or px > peaks[code]:
                    peaks[code] = px

        # Rebalance: only execute the FULL rebalance schedule (not daily adds)
        if current in schedule_map:
            targets = schedule_map[current]
            if targets:  # Full rebalance
                value = cash + sum(q * last_close.get(c, 0) for c, q in shares.items())
                desired = {}
                for code, weight in targets.items():
                    row = bars[code].loc[current] if current in bars[code].index else None
                    ref = row["open"] if row is not None and pd.notna(row["open"]) else last_close.get(code)
                    if ref and ref > 0:
                        desired[code] = math.floor(value * weight / ref / 100) * 100
                
                # Sell everything that's not in the new selection
                pending = {c: -q for c, q in shares.items() if c not in desired}
                # Buy everything in the new selection that we don't already hold
                for c, q in desired.items():
                    held = shares.get(c, 0)
                    if held < q:
                        pending[c] = q - held
                
                # Execute sells then buys
                for side in ("sell", "buy"):
                    for code in sorted(list(pending)):
                        qty = pending[code]
                        if (side=="sell" and qty>=0) or (side=="buy" and qty<=0): continue
                        frame = bars.get(code)
                        row = frame.loc[current] if frame is not None and current in frame.index else None
                        if not can_trade(row, side): continue
                        slip = COST.slippage
                        fill = row["open"] * (1 - slip if side=="sell" else 1 + slip)
                        trade_qty = abs(qty)
                        if side == "buy":
                            affordable = math.floor(cash / (fill * (1+COST.commission)) / 100) * 100
                            trade_qty = min(trade_qty, affordable)
                            if trade_qty <= 0: continue
                        gross = trade_qty * fill
                        fee = max(COST.minimum, gross * COST.commission) + (gross * COST.stamp if side=="sell" else 0)
                        if side == "buy":
                            cash -= gross + fee
                            shares[code] = shares.get(code,0) + trade_qty
                            if code not in position_entry:
                                position_entry[code] = current
                        else:
                            cash += gross - fee
                            shares[code] = shares.get(code,0) - trade_qty
                            position_entry.pop(code, None)
                        pending[code] -= trade_qty if side=="buy" else -trade_qty
                        if pending.get(code)==0: pending.pop(code,None)
                        if shares.get(code)==0: shares.pop(code,None)

        # Stop-loss (optional)
        if use_sl and sl_pct > 0:
            for code, qty in list(shares.items()):
                if qty == 0: continue
                px = last_close.get(code); peak = peaks.get(code)
                if px and peak and peak > 0 and px/peak - 1 < -sl_pct:
                    # Sell via market open next day... but we're in the daily loop
                    # For simplicity, sell at current close
                    gross = qty * px
                    fee = max(COST.minimum, gross * COST.commission) + gross * COST.stamp
                    cash += gross - fee
                    del shares[code]
                    peaks.pop(code, None)
                    position_entry.pop(code, None)
                    sl_events.append({"code": code, "date": str(current.date()), "dd": round(px/peak - 1, 4)})

        # Quarterly invalidation (exit when thesis breaks)
        if use_qi:
            d_str = str(current.date())
            for code, qty in list(shares.items()):
                if qty == 0: continue
                first_inv = qi_first.get(code)
                if first_inv and d_str >= first_inv:
                    # Sell at current close
                    px = last_close.get(code)
                    if px and px > 0:
                        gross = qty * px
                        fee = max(COST.minimum, gross * COST.commission) + gross * COST.stamp
                        cash += gross - fee
                        del shares[code]
                        peaks.pop(code, None)
                        position_entry.pop(code, None)
                        qi_events.append({"code": code, "date": d_str})

        value = cash + sum(q * last_close.get(c, 0) for c, q in shares.items())
        nav_rows.append((current, value / initial))

    return pd.Series(dict(nav_rows)).sort_index()

def build_hold_schedules(sel, cal, top_n, hold_years, delay=0):
    """Build schedules with N-year holding period."""
    signals = sorted(sel.signal_date.unique())
    result = []
    for i, signal in enumerate(signals):
        if i % hold_years != 0:
            continue  # Only rebalance every N years
        later = cal[cal > pd.Timestamp(signal)]
        if len(later) <= delay: continue
        exec_date = later[delay]
        group = sel[sel.signal_date == signal].sort_values("score", ascending=False).head(top_n)
        weights = pd.Series(1/len(group), index=group.index)
        result.append((exec_date, dict(zip(group["code"], weights))))
    return result

# Test: what's the PERFORMANCE of selections held for different periods?
print("\n=== Holding Period Test (top15 + stop-loss + qi) ===")
print(f"\nBenchmark CSI800: CAGR {metric(csi800.loc[calendar,'close'].dropna()/csi800.loc[calendar,'close'].dropna().iloc[0])['cagr']*100:.2f}%")

results = []
for hold_yrs in [1, 2, 3, 5]:
    scheds = build_hold_schedules(selections, calendar, 15, hold_yrs)
    if not scheds: continue
    nav = simulate(calendar, bars, scheds, 10_000_000)
    m = metric(nav)
    results.append((hold_yrs, m))

print(f"\n{'Hold':>6s} {'CAGR':>8s} {'TotalRet':>10s} {'MDD':>8s} {'Sharpe':>6s} {'Calmar':>6s}")
print("-" * 55)
for yrs, m in sorted(results, key=lambda x: x[0]):
    print(f"{yrs:>4d}yrs  {m['cagr']*100:>7.2f}% {m['total_return']*100:>9.2f}% {m['max_drawdown']*100:>7.2f}% {m['sharpe']:>5.2f} {m['calmar']:>6.2f}")

# Also test: what if we JUST buy first year's picks and hold forever?
print("\n=== Buy first year's picks and hold forever ===")
first_signal = sorted(selections.signal_date.unique())[0]
first_selection = selections[selections.signal_date == first_signal].sort_values("score", ascending=False).head(15)
print(f"Date: {first_signal}, Picked {len(first_selection)} stocks:")
for _, r in first_selection.iterrows():
    print(f"  {r['code']} ({r['industry']}) score={float(r['score']):.3f}")

# First, show each stock's individual return
stock_returns = []
for _, r in first_selection.iterrows():
    code = r["code"]
    b = bars.get(code)
    if b is not None:
        prices = b["close"].dropna()
        prices = prices[prices.index >= "2016-05-01"]
        if len(prices) > 100:
            ret = prices.iloc[-1] / prices.iloc[0] - 1
            stock_returns.append((ret, code, r.get("industry",""), prices))

stock_returns.sort(reverse=True)
print(f"\nIndividual stock returns (buy 2016-05, hold to 2024-12):")
for ret, code, ind, _ in stock_returns:
    print(f"  {code:>8s} ({ind:20s}): {ret*100:+7.2f}%")

# Portfolio return (equal weight)
if stock_returns:
    nav = pd.Series(1.0, index=calendar)
    # Find the equal-weight portfolio NAV
    portfolio = {}
    for _, _, _, prices in stock_returns:
        code_returns = prices[prices.index >= "2016-05-01"].ffill()
        code_returns = code_returns / code_returns.iloc[0]
        portfolio[code_returns.index[0]] = code_returns

    # Min-max align dates
    all_dates = []
    for p in stock_returns:
        all_dates.extend(p[3].index.tolist())
    all_dates = sorted(set(all_dates))
    
    # Simpler: just compute buy & hold portfolio
    rets = [s[0] for s in stock_returns]
    avg_ret = sum(rets) / len(rets)
    
    # Equal weight: 1/15 each
    ewd_ret = sum(max(r, -1.0) for r in rets) / len(rets)  # cap losses at -100%
    
    # Compute CAGR from total return
    years = 8.67  # 2016-05 to 2024-12
    eq_cagr = (1 + ewd_ret) ** (1/years) - 1
    
    # Best and worst
    best = max(rets)
    worst = min(rets)
    
    print(f"\nEqual-weight portfolio (15 stocks, buy 2016 hold to 2024):")
    print(f"  Total return: {ewd_ret*100:+.2f}%")
    print(f"  CAGR: {eq_cagr*100:+.2f}%")
    print(f"  Best stock: {best*100:+.2f}%")
    print(f"  Worst stock: {worst*100:+.2f}%")
    
    # Best individual holdings
    winners = [s for s in stock_returns if s[0] > 0.5]
    losers = [s for s in stock_returns if s[0] < -0.5]
    print(f"\n  Winners (>+50%): {len(winners)}/{len(stock_returns)}")
    for ret, code, ind, _ in winners:
        print(f"    {code} ({ind}): {ret*100:+.2f}%")
    print(f"  Losers (<-50%): {len(losers)}/{len(stock_returns)}")
    for ret, code, ind, _ in losers:
        print(f"    {code} ({ind}): {ret*100:+.2f}%")

OUT.mkdir(exist_ok=True)
print(f"\nDone.")
