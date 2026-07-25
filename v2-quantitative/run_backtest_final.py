#!/usr/bin/env python3
"""Final strategy: 2-year hold + composite score + QI. No stop-loss."""
from __future__ import annotations

import json, math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import pandas as pd

HERE = Path(__file__).resolve().parent; DATA = HERE / "data"; OUT = HERE / "output"
SNAPSHOTS = HERE / "data" / "financial_snapshots"

@dataclass
class Cost:
    commission: float = 0.00025; minimum: float = 5; slippage: float = 5/10000; stamp: float = 0.001
COST = Cost()

def load_bars(path):
    try:
        f = pd.read_csv(path, dtype={"code":str,"tradestatus":str,"isST":str})
        f["date"] = pd.to_datetime(f.date)
        for c in ("open","high","low","close","volume","pctChg"):
            f[c] = pd.to_numeric(f[c], errors="coerce")
        return f.set_index("date").sort_index()
    except: return pd.DataFrame()

def metric(nav):
    nav = nav.dropna()
    if len(nav) < 2: return {"cagr":0,"max_drawdown":0}
    years = (nav.index[-1]-nav.index[0]).days/365.25
    rets = nav.pct_change().dropna(); peak = nav.cummax(); dd = nav/peak-1
    cagr = (nav.iloc[-1]/nav.iloc[0])**(1/years)-1
    vol = rets.std()*math.sqrt(252)
    sharpe = rets.mean()/rets.std()*math.sqrt(252) if rets.std() else 0
    mdd = dd.min()
    return {"cagr":cagr,"volatility":vol,"sharpe":sharpe,"max_drawdown":mdd,
            "calmar":cagr/abs(mdd) if mdd and abs(mdd)>0.001 else None,
            "total_return":nav.iloc[-1]/nav.iloc[0]-1}

def can_trade(row, side):
    if row is None or row["tradestatus"]!="1" or row["volume"]<=0: return False
    op = abs(row["high"]-row["low"])<1e-9
    if side=="buy" and op and row["pctChg"]>=4.8: return False
    if side=="sell" and op and row["pctChg"]<=-4.8: return False
    return True

# Load
csi800 = load_bars(DATA/"prices"/"CSI800.csv")
calendar = csi800.index[(csi800.index>="2016-05-01")&(csi800.index<="2024-12-31")]
selections = pd.read_csv(DATA/"selections.csv", dtype={"code":str})
selections["code"] = selections.code.str.zfill(6)
codes = set(selections.code.unique())
bars = {}
for c in codes:
    p = DATA/"prices"/f"{c}.csv"
    if p.exists():
        b = load_bars(p)
        if len(b)>100: bars[c]=b

# Build N-year hold schedules
def build_schedules(sel, cal, top_n, hold_yrs, delay=0):
    signals = sorted(sel.signal_date.unique())
    result = []
    for i in range(0, len(signals), hold_yrs):
        signal = signals[i]
        later = cal[cal > pd.Timestamp(signal)]
        if len(later) <= delay: continue
        exec_date = later[delay]
        group = sel[sel.signal_date==signal].sort_values("score", ascending=False).head(top_n)
        if len(group) < 3: continue
        result.append((exec_date, dict(zip(group["code"], pd.Series(1/len(group), index=group.index)))))
    return result

# Simulate
def simulate(cal, scheds, initial):
    cash, shares = initial, {}
    nav_rows, peaks = [], {}
    schedule_map = {d: targets for d, targets in scheds}
    last_close = {}
    for current in cal:
        for c, frame in bars.items():
            if current in frame.index and pd.notna(frame.at[current,"close"]):
                px = frame.at[current,"close"]; last_close[c] = px
                if c not in peaks or px > peaks[c]: peaks[c] = px
        if current in schedule_map:
            targets = schedule_map[current]
            value = cash + sum(q*last_close.get(c,0) for c,q in shares.items())
            desired = {}
            for c, w in targets.items():
                row = bars[c].loc[current] if c in bars and current in bars[c].index else None
                ref = row["open"] if row is not None and pd.notna(row.get("open")) else last_close.get(c)
                if ref and ref > 0:
                    desired[c] = math.floor(value * w / ref / 100) * 100
            pending = {c: -q for c,q in shares.items() if c not in desired}
            for c, q in desired.items():
                held = shares.get(c,0)
                if held < q: pending[c] = q - held
            for side in ("sell","buy"):
                for c in sorted(list(pending)):
                    qty = pending[c]
                    if (side=="sell" and qty>=0) or (side=="buy" and qty<=0): continue
                    frame = bars.get(c)
                    row = frame.loc[current] if frame is not None and current in frame.index else None
                    if not can_trade(row, side): continue
                    slip = COST.slippage
                    fill = row["open"]*(1-slip if side=="sell" else 1+slip)
                    tq = abs(qty)
                    if side == "buy":
                        aff = math.floor(cash/(fill*(1+COST.commission))/100)*100
                        tq = min(tq, aff)
                        if tq <= 0: continue
                    gross = tq*fill
                    fee = max(COST.minimum, gross*COST.commission)+(gross*COST.stamp if side=="sell" else 0)
                    if side=="buy":
                        cash -= gross+fee; shares[c] = shares.get(c,0)+tq
                    else:
                        cash += gross-fee; shares[c] = shares.get(c,0)-tq
                    pending[c] -= tq if side=="buy" else -tq
                    if pending.get(c)==0: pending.pop(c,None)
                    if shares.get(c)==0: shares.pop(c,None)
        value = cash + sum(q*last_close.get(c,0) for c,q in shares.items())
        nav_rows.append((current, value/initial))
    return pd.Series(dict(nav_rows)).sort_index()

# Run variants
variants = [
    ("top15_hy1", 1, 15),   # 1-year hold (baseline)
    ("top15_hy2", 2, 15),   # 2-year hold (the new strategy)
    ("top10_hy2", 2, 10),   # 2-year hold + 10 positions
]

bm = csi800.loc[calendar,"close"].dropna(); bm = bm/bm.iloc[0]; bm_m = metric(bm)
print(f"Benchmark CSI800: CAGR {bm_m['cagr']*100:.2f}%")
for name, hy, tn in variants:
    scheds = build_schedules(selections, calendar, tn, hy)
    if not scheds: continue
    nav = simulate(calendar, scheds, 10_000_000)
    m = metric(nav)
    ex = m["cagr"] - bm_m["cagr"]
    print(f"  {name}: CAGR {m['cagr']*100:+.2f}% | excess {ex*100:+.2f}% | MDD {m['max_drawdown']*100:.2f}% | Sharpe {m['sharpe']:.2f} | total {m['total_return']*100:.1f}%")

OUT.mkdir(exist_ok=True)
d = {"benchmark": bm_m, "generated_at": datetime.now().isoformat(),
     "variants": {k: metric(v) for k,v in [("top15_hy1", simulate(calendar, build_schedules(selections, calendar, 15, 1), 10_000_000)),
                                            ("top15_hy2", simulate(calendar, build_schedules(selections, calendar, 15, 2), 10_000_000)),
                                            ("top10_hy2", simulate(calendar, build_schedules(selections, calendar, 10, 2), 10_000_000))]}}
(OUT/"summary_final.json").write_text(json.dumps(d, ensure_ascii=False, indent=2))
print(f"\nSaved to summary_final.json")
