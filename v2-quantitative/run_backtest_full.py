#!/usr/bin/env python3
"""
Full backtest: composite score + stop-loss + quarterly invalidation + valuation exit.
"""
from __future__ import annotations

import csv, json, math
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
    commission: float; minimum: float; slippage: float; stamp: float

COST = Cost(0.00025, 5, 5/10000, 0.001)

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
    if len(nav) < 2: return {"cagr": 0, "max_drawdown": 0, "sharpe": 0}
    years = (nav.index[-1] - nav.index[0]).days / 365.25
    returns = nav.pct_change().dropna()
    peak = nav.cummax(); dd = nav / peak - 1
    cagr = (nav.iloc[-1] / nav.iloc[0]) ** (1 / years) - 1
    vol = returns.std() * math.sqrt(252)
    sharpe = returns.mean() / returns.std() * math.sqrt(252) if returns.std() else 0
    mdd = dd.min()
    return {"start": str(nav.index[0].date()), "end": str(nav.index[-1].date()),
            "cagr": cagr, "volatility": vol, "sharpe": sharpe,
            "max_drawdown": mdd, "calmar": cagr / abs(mdd) if mdd and abs(mdd) > 0.001 else None}

def can_trade(row, side):
    if row is None or row["tradestatus"] != "1" or row["volume"] <= 0: return False
    one_price = abs(row["high"] - row["low"]) < 1e-9
    if side == "buy" and one_price and row["pctChg"] >= 4.8: return False
    if side == "sell" and one_price and row["pctChg"] <= -4.8: return False
    return True

# --- Step 1: Load ALL quarterly data for invalidation ---
print("Loading quarterly data for invalidation...")
all_quarters = []
for snap in sorted(SNAPSHOTS.glob("20*.csv")):
    df = pd.read_csv(snap, dtype={"SECURITY_CODE": str, "NOTICE_DATE": str})
    for c in ["YSTZ","SJLTZ"]:
        if c in df.columns: df[c] = pd.to_numeric(df[c], errors="coerce")
    df["code"] = df.SECURITY_CODE.str.zfill(6)
    df["report_date"] = pd.to_datetime(df.REPORTDATE, errors="coerce")
    df["notice_date"] = pd.to_datetime(df.NOTICE_DATE, errors="coerce")
    all_quarters.append(df[["code","report_date","notice_date","YSTZ","SJLTZ"]])
all_q = pd.concat(all_quarters, ignore_index=True) if all_quarters else pd.DataFrame()
# For each company, sort by report_date
all_q = all_q.sort_values(["code","report_date"]).dropna(subset=["YSTZ","SJLTZ","notice_date"])
q_by_code = defaultdict(list)
for _, r in all_q.iterrows():
    q_by_code[r["code"]].append((r["report_date"], r["notice_date"], r["YSTZ"], r["SJLTZ"]))
for c in q_by_code: q_by_code[c].sort(key=lambda x: x[0])

def check_quarterly_invalid(code, date):
    """Check if last 2 quarters both have negative revenue_yoy AND profit_yoy."""
    if code not in q_by_code: return False
    qs = [q for q in q_by_code[code] if q[1] <= pd.Timestamp(date)]
    if len(qs) < 2: return False
    q1, q2 = qs[-1], qs[-2]
    return (q1[2] < 0 and q1[3] < 0 and q2[2] < 0 and q2[3] < 0)

# --- Step 2: Precompute quarterly invalidation monthly ---
print("Precomputing invalidation signals...")
codes_with_q = set(all_q.code.unique())
monthly_dates = pd.date_range("2016-06-01", "2024-12-31", freq="ME")
qi_cache = {}  # (code, date_str) -> bool
for d in monthly_dates:
    for code in codes_with_q:
        if check_quarterly_invalid(code, d):
            qi_cache[(code, str(d.date()))] = True
print(f"  Invalidation signals: {len(qi_cache)}")
# Convert to first_invalid per code for fast look-up
qi_first = {}
for code, d_str in sorted(qi_cache.keys(), key=lambda x: x[1]):
    if code not in qi_first:
        qi_first[code] = d_str
print(f"  Companies with invalidation: {len(qi_first)}")

# --- Step 3: Load selections ---
selections = pd.read_csv(DATA / "selections.csv", dtype={"code": str})
selections["code"] = selections.code.str.zfill(6)
selections["signal_date"] = selections.signal_date.astype(str)
all_codes = set(selections.code.unique())

# --- Step 4: Load price bars ---
print("Loading price bars...")
bars = {}
for code in all_codes:
    p = DATA / "prices" / f"{code}.csv"
    if p.exists():
        b = load_bars(p)
        if len(b) > 100: bars[code] = b
print(f"  {len(bars)}/{len(all_codes)} loaded")

# --- Step 5: Precompute valuation exit ---
print("Precomputing valuation exits...")
# Get annual EPS from snapshots (only annual reports: Dec 31)
eps_data = all_q[all_q.report_date.dt.month == 12].groupby("code").apply(
    lambda g: g.set_index("report_date")["YSTZ"].to_dict() if len(g) > 0 else {})
# Actually use basic_eps from annual snapshots
all_annual = []
for snap in sorted(SNAPSHOTS.glob("20*.csv")):
    df = pd.read_csv(snap, dtype={"SECURITY_CODE": str})
    if "BASIC_EPS" in df.columns: df["BASIC_EPS"] = pd.to_numeric(df["BASIC_EPS"], errors="coerce")
    df["code"] = df.SECURITY_CODE.str.zfill(6)
    df["report_date"] = pd.to_datetime(df.REPORTDATE, errors="coerce")
    all_annual.append(df[df.report_date.dt.month == 12][["code","report_date","BASIC_EPS"]])
ann = pd.concat(all_annual, ignore_index=True).dropna()
eps_by_code = defaultdict(dict)
for _, r in ann.iterrows():
    eps_by_code[r["code"]][str(r["report_date"].year)] = r["BASIC_EPS"]

# For each company+date, compute trailing 252-day PE percentile
# Simplified: use annual EPS as the denominator
ve_cache = {}  # (code, date) -> should_exit
for code, bar_df in bars.items():
    closes = bar_df["close"].dropna()
    pe_history = []
    current_eps = 0
    last_eps_update = ""
    for date_str, px in closes.items():
        dt = pd.Timestamp(date_str)
        yr_month = f"{dt.year}"
        # Use most recent annual EPS (available before current date)
        best_eps = 0
        for ey in range(dt.year - 1, dt.year - 3, -1):
            if str(ey) in eps_by_code.get(code, {}):
                best_eps = eps_by_code[code][str(ey)]
                break
        if best_eps <= 0: continue
        pe = px / best_eps
        if pe < 3 or pe > 500: continue  # filter noise
        pe_history.append((date_str, pe))
        if len(pe_history) > 252: pe_history.pop(0)
        if len(pe_history) >= 60:  # need ~3 months of data
            pct = sum(1 for _, v in pe_history if v < pe) / len(pe_history)
            if pct >= 0.80:  # PE in top 20% of trailing year
                ve_cache[(code, str(date_str.date()))] = True
print(f"  Valuation exit signals: {len(ve_cache)}")

# --- Step 6: Enhanced simulate ---
def simulate(calendar, schedules, initial, top_n, stop_loss, use_qi, use_ve):
    cash, shares, pending = initial, {}, {}
    nav_rows, events = [], []
    peaks = {}
    schedule_map = {d: targets for d, targets in schedules}
    last_close = {}

    for current in calendar:
        for code, frame in bars.items():
            if current in frame.index and pd.notna(frame.at[current, "close"]):
                px = frame.at[current, "close"]
                last_close[code] = px
                if code not in peaks or px > peaks[code]: peaks[code] = px

        # Rebalance
        if current in schedule_map:
            targets = schedule_map[current]
            value = cash + sum(q * last_close.get(c, 0) for c, q in shares.items())
            desired = {}
            for code, weight in targets.items():
                row = bars[code].loc[current] if current in bars[code].index else None
                ref = row["open"] if row is not None and pd.notna(row["open"]) else last_close.get(code)
                if ref and ref > 0:
                    desired[code] = math.floor(value * weight / ref / 100) * 100
            pending = {c: q - shares.get(c, 0) for c, q in desired.items()}
            for code, qty in shares.items():
                if code not in desired and qty: pending[code] = -qty
            for code in desired:
                peaks.pop(code, None)

        # Stop-loss check
        if stop_loss > 0:
            for code, qty in list(shares.items()):
                if qty == 0: continue
                px = last_close.get(code); peak = peaks.get(code)
                if px and peak and peak > 0 and px/peak - 1 < -stop_loss:
                    if code not in pending or pending[code] >= 0: pending[code] = -qty

        # Quarterly invalidation (optimized: only check first invalid date per code)
        if use_qi:
            for code, qty in list(shares.items()):
                if qty == 0: continue
                d_str = str(current.date())
                first_inv = qi_first.get(code)
                if first_inv and d_str >= first_inv:
                    if code not in pending or pending[code] >= 0: pending[code] = -qty

        # Valuation exit
        if use_ve:
            for code, qty in list(shares.items()):
                if qty == 0: continue
                d_str = str(current.date())
                if (code, d_str) in ve_cache:
                    if code not in pending or pending[code] >= 0: pending[code] = -qty
        
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
                    cash -= gross + fee; shares[code] = shares.get(code,0) + trade_qty
                else:
                    cash += gross - fee; shares[code] = shares.get(code,0) - trade_qty
                pending[code] -= trade_qty if side=="buy" else -trade_qty
                if pending.get(code)==0: pending.pop(code,None)
                if shares.get(code)==0: shares.pop(code,None)

        value = cash + sum(q * last_close.get(c,0) for c,q in shares.items())
        nav_rows.append((current, value / initial))
    return pd.Series(dict(nav_rows)).sort_index()

# --- Step 7: Build schedules and run ---
def build_schedules(sel, calendar, top_n, delay=0):
    result = []
    for signal, group in sel.groupby("signal_date"):
        later = calendar[calendar > pd.Timestamp(signal)]
        if len(later) <= delay: continue
        exec_date = later[delay]
        group = group.sort_values("score", ascending=False).head(top_n)
        weights = pd.Series(1/len(group), index=group.index)
        result.append((exec_date, dict(zip(group["code"], weights))))
    return result

csi800 = load_bars(DATA / "prices" / "CSI800.csv")
calendar = csi800.index[(csi800.index >= "2016-05-01") & (csi800.index <= "2024-12-31")]

# Test combinations
test_cases = [
    ("top15", 15, 0.00, False, False),
    ("top15_sl15", 15, 0.15, False, False),
    ("top15_qi", 15, 0.00, True, False),
    ("top15_ve", 15, 0.00, False, True),
    ("top15_qi_ve", 15, 0.00, True, True),
    ("top15_sl15_qi", 15, 0.15, True, False),
    ("top15_sl15_ve", 15, 0.15, False, True),
    ("top15_sl15_qi_ve", 15, 0.15, True, True),
    ("top10_sl15_qi_ve", 10, 0.15, True, True),
]

bm = csi800.loc[calendar, "close"].dropna()
bm = bm / bm.iloc[0]
bm_m = metric(bm)

print(f"\nBenchmark CSI800: CAGR {bm_m['cagr']*100:.2f}% | MDD {bm_m['max_drawdown']*100:.2f}% | Sharpe {bm_m['sharpe']:.2f}")
print(f"\n{'Variant':25s} {'CAGR':>8s} {'Excess':>8s} {'MDD':>8s} {'Sharpe':>6s} {'Calmar':>6s}")
print("-" * 70)

all_navs = {}
for name, tn, sl, qi, ve in test_cases:
    scheds = build_schedules(selections, calendar, tn)
    if not scheds: print(f"{name:25s}: NO SCHEDULES"); continue
    nav = simulate(calendar, scheds, 10_000_000, tn, sl, qi, ve)
    m = metric(nav)
    ex = m["cagr"] - bm_m["cagr"]
    cal_s = f"{m['calmar']:.2f}" if m.get('calmar') else "N/A"
    print(f"{name:25s} {m['cagr']*100:>7.2f}% {ex*100:>7.2f}% {m['max_drawdown']*100:>7.2f}% {m['sharpe']:>5.2f} {cal_s:>6s}")
    all_navs[name] = nav

OUT.mkdir(exist_ok=True)
result = {"benchmark": bm_m, "generated_at": datetime.now().isoformat()}
result["variants"] = {k: metric(v) for k,v in all_navs.items()}
(OUT / "summary_full.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
print(f"\nSaved: {OUT / 'summary_full.json'}")

# Save NAV for best 3 variants
nav_df = pd.DataFrame({k: v.reindex(calendar).ffill() for k,v in all_navs.items()
                       if k in ["top15_sl15_qi_ve","top15_sl15","top15"]})
nav_df.to_csv(OUT / "nav_full.csv", index_label="date")
print("NAV saved.")
