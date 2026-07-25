#!/usr/bin/env python3
"""Simple holding period test: buy top 15 each year, hold for N years."""
import pandas as pd
from pathlib import Path
import math

prices_dir = Path("回测/pit/data/prices")
selections = pd.read_csv("回测/pit/data/selections.csv", dtype={"code": str})
selections["code"] = selections.code.str.zfill(6)

# Get unique signal dates
signals = sorted(selections.signal_date.unique())
print(f"Signal dates: {signals[0]} to {signals[-1]} ({len(signals)} cohorts)")

def buy_and_hold_return(code, buy_date, sell_date):
    """Return from buy_date to sell_date for a stock. Returns None if insufficient data."""
    f = prices_dir / f"{code}.csv"
    if not f.exists(): return None, None
    df = pd.read_csv(f)
    df["date"] = pd.to_datetime(df.date)
    buy = df[df.date >= buy_date].sort_values("date")
    if len(buy) < 10: return None, None
    buy_px = buy.close.iloc[0]
    sell = df[df.date <= sell_date].sort_values("date")
    if len(sell) < 10: return None, None
    sell_px = sell.close.iloc[-1]
    if buy_px <= 0 or sell_px <= 0: return None, None
    ret = sell_px / buy_px - 1
    total_days = (pd.Timestamp(sell.date.iloc[-1]) - pd.Timestamp(buy.date.iloc[0])).days
    years = total_days / 365.25
    return ret, years

print(f"\n{'Hold':>8s} {'CAGR':>8s} {'Total':>8s} {'Cohorts':>10s}")
print("-" * 40)

hold_periods = [1, 2, 3, 5, 9.5]  # years  # 9.5 = hold forever

for hold_yrs in hold_periods:
    total_value = 1.0
    cohort_count = 0
    
    for i, signal in enumerate(signals):
        buy_date = pd.Timestamp(signal)
        if hold_yrs >= 9:
            sell_date = pd.Timestamp("2024-12-31")
        else:
            sell_date = buy_date + pd.DateOffset(years=hold_yrs)
            if sell_date > pd.Timestamp("2024-12-31"):
                continue
        
        top15 = selections[selections.signal_date == signal].sort_values("score", ascending=False).head(15)
        cohort_rets = []
        for _, r in top15.iterrows():
            ret, years = buy_and_hold_return(r["code"], buy_date, sell_date)
            if ret is not None and years > hold_yrs * 0.5:
                cohort_rets.append((ret, years))
        
        if not cohort_rets:
            continue
        
        # Equal-weight portfolio return for this cohort
        capped_rets = [max(r, -1) for r, _ in cohort_rets]
        avg_ret = sum(capped_rets) / len(capped_rets)
        total_value *= (1 + avg_ret)
        cohort_count += 1
        
        if hold_yrs == 3 and i < 3:  # Debug first 3 cohorts
            pass  # Just track total
    
    if cohort_count > 0:
        total_days = (pd.Timestamp("2024-12-31") - pd.Timestamp(signals[0])).days
        total_years = total_days / 365.25
        total_cagr = total_value ** (1 / total_years) - 1
        
        label = f"{int(hold_yrs)}yr" if hold_yrs < 9 else "forever"
        print(f"{label:>8s} {total_cagr*100:>7.2f}% {total_value*100:>7.2f}% {cohort_count:>5d} cohorts")

# Also show detailed cohort-level returns for 3yr hold
print(f"\n=== 3-YEAR HOLD: Detailed cohort returns ===")
for i, signal in enumerate(signals):
    buy_date = pd.Timestamp(signal)
    sell_date = buy_date + pd.DateOffset(years=3)
    if sell_date > pd.Timestamp("2024-12-31"):
        break
    top15 = selections[selections.signal_date == signal].sort_values("score", ascending=False).head(15)
    rets = []
    for _, r in top15.iterrows():
        ret, yrs = buy_and_hold_return(r["code"], buy_date, sell_date)
        if ret is not None:
            rets.append(max(ret, -1))
    if rets:
        avg = sum(rets) / len(rets)
        print(f"  {signal} → {sell_date.date()}: {avg*100:+7.2f}% ({len(rets)} stocks)")

# Show 5-YEAR HOLD cohorts
print(f"\n=== 5-YEAR HOLD: Detailed cohort returns ===")
for i, signal in enumerate(signals):
    buy_date = pd.Timestamp(signal)
    sell_date = buy_date + pd.DateOffset(years=5)
    if sell_date > pd.Timestamp("2024-12-31"):
        break
    top15 = selections[selections.signal_date == signal].sort_values("score", ascending=False).head(15)
    rets = []
    for _, r in top15.iterrows():
        ret, yrs = buy_and_hold_return(r["code"], buy_date, sell_date)
        if ret is not None:
            rets.append(max(ret, -1))
    if rets:
        avg = sum(rets) / len(rets)
        print(f"  {signal} → {sell_date.date()}: {avg*100:+7.2f}% ({len(rets)} stocks)")

# And what about holding the FIRST cohort forever?
print(f"\n=== BUY & HOLD first cohort (2015-05-01) forever ===")
first = selections[selections.signal_date == signals[0]].sort_values("score", ascending=False).head(15)
rets = []
for _, r in first.iterrows():
    ret, yrs = buy_and_hold_return(r["code"], pd.Timestamp(signals[0]), pd.Timestamp("2024-12-31"))
    if ret is not None:
        rets.append((ret, max(ret, -1), r["code"]))
if rets:
    avg = sum(r[1] for r in rets) / len(rets)
    total_days = (pd.Timestamp("2024-12-31") - pd.Timestamp(signals[0])).days
    total_years = total_days / 365.25
    cagr = (1 + avg) ** (1 / total_years) - 1
    print(f"  Equal-weight CAGR: {cagr*100:.2f}%")
    print(f"  Total return: {avg*100:.1f}%")
    for ret, _, code in sorted(rets, reverse=True)[:5]:
        print(f"    {code}: +{ret*100:.1f}%")
    for ret, _, code in sorted(rets)[:3]:
        print(f"    {code}: {ret*100:.1f}%")
