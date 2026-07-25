#!/usr/bin/env python3
"""Frozen publication-date-aligned full-universe diagnostic backtest."""
from __future__ import annotations

import csv
import json
import math
import statistics
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd


HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "output"


@dataclass
class Cost:
    commission: float
    minimum: float
    slippage: float
    stamp: float


def load_bars(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype={"code": str, "tradestatus": str, "isST": str})
    frame["date"] = pd.to_datetime(frame["date"])
    for c in ("open", "high", "low", "close", "volume", "pctChg"):
        frame[c] = pd.to_numeric(frame[c], errors="coerce")
    return frame.set_index("date").sort_index()


def metric(nav: pd.Series) -> dict:
    nav = nav.dropna()
    years = (nav.index[-1] - nav.index[0]).days / 365.25
    returns = nav.pct_change().dropna()
    peak = nav.cummax()
    dd = nav / peak - 1
    cagr = (nav.iloc[-1] / nav.iloc[0]) ** (1 / years) - 1
    vol = returns.std() * math.sqrt(252)
    sharpe = returns.mean() / returns.std() * math.sqrt(252) if returns.std() else 0
    mdd = dd.min()
    return {
        "start": nav.index[0].date().isoformat(), "end": nav.index[-1].date().isoformat(),
        "total_return": nav.iloc[-1] / nav.iloc[0] - 1, "cagr": cagr,
        "volatility": vol, "sharpe": sharpe, "max_drawdown": mdd,
        "calmar": cagr / abs(mdd) if mdd else None,
    }


def can_trade(row, side: str) -> bool:
    if row is None or row["tradestatus"] != "1" or row["volume"] <= 0:
        return False
    one_price = abs(row["high"] - row["low"]) < 1e-9
    if side == "buy" and one_price and row["pctChg"] >= 4.8:
        return False
    if side == "sell" and one_price and row["pctChg"] <= -4.8:
        return False
    return True


def simulate(calendar, bars, schedules, initial: float, cost: Cost):
    cash, shares, pending = initial, {}, {}
    nav_rows, events, turnover, blocked = [], [], 0.0, 0
    schedule_map = {d: targets for d, targets in schedules}
    last_close = {}
    for current in calendar:
        for code, frame in bars.items():
            if current in frame.index and pd.notna(frame.at[current, "close"]):
                last_close[code] = frame.at[current, "close"]
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
        # Sells first, then buys. Pending orders carry across blocked sessions.
        for side in ("sell", "buy"):
            for code in sorted(list(pending)):
                qty = pending[code]
                if (side == "sell" and qty >= 0) or (side == "buy" and qty <= 0):
                    continue
                frame = bars.get(code)
                row = frame.loc[current] if frame is not None and current in frame.index else None
                if not can_trade(row, side):
                    blocked += 1
                    continue
                slip = cost.slippage / 10000
                fill = row["open"] * (1 - slip if side == "sell" else 1 + slip)
                trade_qty = abs(qty)
                if side == "buy":
                    affordable = math.floor(cash / (fill * (1 + cost.commission)) / 100) * 100
                    trade_qty = min(trade_qty, affordable)
                    if trade_qty <= 0:
                        continue
                gross = trade_qty * fill
                fee = max(cost.minimum, gross * cost.commission) + (gross * cost.stamp if side == "sell" else 0)
                if side == "buy":
                    cash -= gross + fee
                    shares[code] = shares.get(code, 0) + trade_qty
                    pending[code] -= trade_qty
                else:
                    cash += gross - fee
                    shares[code] = shares.get(code, 0) - trade_qty
                    pending[code] += trade_qty
                turnover += gross
                events.append({
                    "date": current.date().isoformat(), "code": code, "side": side,
                    "shares": trade_qty, "fill": fill, "gross": gross, "fee": fee,
                })
                if pending.get(code) == 0:
                    pending.pop(code, None)
                if shares.get(code) == 0:
                    shares.pop(code, None)
        value = cash + sum(q * last_close.get(c, 0) for c, q in shares.items())
        nav_rows.append((current, value / initial))
    return pd.Series(dict(nav_rows)).sort_index(), events, turnover / initial, blocked


def build_schedules(selections, calendar, top_n: int, score_weight: bool, delay: int):
    rows = selections[selections["top_n"] == top_n].copy()
    result = []
    for signal, group in rows.groupby("signal_date"):
        later = calendar[calendar > pd.Timestamp(signal)]
        if len(later) <= delay:
            continue
        execution = later[delay]
        group = group.head(top_n)
        if score_weight:
            weights = group["score"] / group["score"].sum()
        else:
            weights = pd.Series(1 / len(group), index=group.index)
        result.append((execution, dict(zip(group["code"], weights))))
    return result


def cohort_hit_rate(selections, bars, benchmark):
    periods, wins, total = [], 0, 0
    rows = selections[selections["top_n"] == 20]
    signals = sorted(pd.to_datetime(rows["signal_date"].unique()))
    for index, signal in enumerate(signals[:-1]):
        end = signals[index + 1]
        benchmark_slice = benchmark[(benchmark.index > signal) & (benchmark.index <= end)]
        if len(benchmark_slice) < 2:
            continue
        bench_return = benchmark_slice["close"].iloc[-1] / benchmark_slice["open"].iloc[0] - 1
        for code in rows[rows["signal_date"] == signal.strftime("%Y-%m-%d")]["code"]:
            frame = bars[code]
            sample = frame[(frame.index > signal) & (frame.index <= end)]
            if len(sample) < 2:
                continue
            stock_return = sample["close"].iloc[-1] / sample["open"].iloc[0] - 1
            won = stock_return > bench_return
            wins += int(won)
            total += 1
            periods.append({"signal_date": signal.date().isoformat(), "code": code,
                            "stock_return": stock_return, "benchmark_return": bench_return,
                            "excess_win": won})
    return wins / total if total else None, periods


def main() -> int:
    plan = json.loads((HERE / "experiment-plan.json").read_text(encoding="utf-8"))
    selections = pd.read_csv(DATA / "selections.csv", dtype={"code": str})
    selections["code"] = selections["code"].str.zfill(6)
    selections["signal_date"] = selections["signal_date"].astype(str)
    codes = sorted(selections["code"].unique())
    bars = {code: load_bars(DATA / "prices" / f"{code}.csv") for code in codes}
    csi800, csi300 = load_bars(DATA / "prices" / "CSI800.csv"), load_bars(DATA / "prices" / "CSI300.csv")
    calendar = csi800.index[(csi800.index >= "2016-05-01") & (csi800.index <= "2024-12-31")]
    costs = {
        k: Cost(v["commission"], v["minimum_commission"], v["slippage_bps"], v["sell_stamp_tax"])
        for k, v in plan["costs"].items()
    }
    specs = {
        "V0": (20, False, 0, "base"), "V1": (20, True, 0, "base"),
        "V2": (10, False, 0, "base"), "V3": (40, False, 0, "base"),
        "V4": (20, False, 0, "double"), "V5": (20, False, 20, "base"),
    }
    navs, summaries, all_events = {}, {}, []
    for key, (top_n, scored, delay, cost_name) in specs.items():
        schedules = build_schedules(selections, calendar, top_n, scored, delay)
        nav, events, turnover, blocked = simulate(calendar, bars, schedules, 10_000_000, costs[cost_name])
        navs[key] = nav
        summaries[key] = {**metric(nav), "turnover_multiple": turnover,
                          "trade_count": len(events), "blocked_order_days": blocked}
        all_events.extend([{"variant": key, **event} for event in events])
    benchmark_nav = csi800.loc[calendar, "close"].dropna()
    benchmark_nav = benchmark_nav / benchmark_nav.iloc[0]
    context_nav = csi300.reindex(calendar)["close"].dropna()
    context_nav = context_nav / context_nav.iloc[0]
    benchmark_metrics = metric(benchmark_nav)
    for item in summaries.values():
        item["excess_cagr_vs_CSI800"] = item["cagr"] - benchmark_metrics["cagr"]
    slices = {}
    for label, start, end in (
        ("development", "2016-05-01", "2021-12-31"),
        ("historical_observed_validation", "2022-01-01", "2024-12-31"),
    ):
        slices[label] = {
            key: metric(nav[(nav.index >= start) & (nav.index <= end)])
            for key, nav in {**navs, "CSI800": benchmark_nav}.items()
        }
    hit_rate, cohort_rows = cohort_hit_rate(selections, bars, csi800)
    gate_checks = {
        "V0 positive CAGR": bool(summaries["V0"]["cagr"] > 0),
        "V0 positive excess full": bool(summaries["V0"]["excess_cagr_vs_CSI800"] > 0),
        "V0 positive excess development": bool(slices["development"]["V0"]["cagr"] > slices["development"]["CSI800"]["cagr"]),
        "V0 positive excess historical validation": bool(slices["historical_observed_validation"]["V0"]["cagr"] > slices["historical_observed_validation"]["CSI800"]["cagr"]),
        "V0 Calmar above benchmark": bool(summaries["V0"]["calmar"] > benchmark_metrics["calmar"]),
        "V4 double-cost positive excess": bool(summaries["V4"]["excess_cagr_vs_CSI800"] > 0),
        "strict PIT version provenance": False,
    }
    result = {
        "generated_at": datetime.now().astimezone().isoformat(), "variants": summaries,
        "benchmark": {"name": "CSI800 price index", **benchmark_metrics},
        "context": {"name": "CSI300 price index", **metric(context_nav)},
        "slices": slices, "selection_excess_hit_rate": hit_rate,
        "gate": {"passed": all(gate_checks.values()), "checks": gate_checks},
        "verdict": {"model_risk": "待审计", "research_process_risk": "阻断级",
                    "action": "仅研究", "reason": "公开源无法证明原始版本财务数值；未来真样本外尚未发生"},
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    pd.DataFrame({k: v.reindex(calendar).ffill() for k, v in {**navs, "CSI800": benchmark_nav}.items()}).to_csv(OUT / "nav_variants.csv", index_label="date")
    pd.DataFrame(all_events).to_csv(OUT / "events.csv", index=False)
    pd.DataFrame(cohort_rows).to_csv(OUT / "cohort_outcomes.csv", index=False)
    pd.DataFrame([{"variant": k, **v} for k, v in summaries.items()]).to_csv(OUT / "metrics.csv", index=False)
    lines = ["# 历史全A公告日对齐回测", "", f"晋级门槛：**{'通过' if result['gate']['passed'] else '未通过'}**", "",
             "|变体|CAGR|中证800超额CAGR|最大回撤|Sharpe|Calmar|换手倍数|",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for key, x in summaries.items():
            calmar_val = x.get("calmar")
    lines.append(f"|{key}|{x['cagr']:.2%}|{x['excess_cagr_vs_CSI800']:.2%}|{x['max_drawdown']:.2%}|{x['sharpe']:.2f}|{calmar_str}|{x['turnover_multiple']:.1f}|")
    lines += ["", f"- 中证800 CAGR：{benchmark_metrics['cagr']:.2%}",
              f"- 股票期选股超额胜率：{hit_rate:.2%}" if hit_rate is not None else "- 胜率不可用",
              "", "## 历史切片",
              f"- 2016–2021 V0 / 中证800 CAGR：{slices['development']['V0']['cagr']:.2%} / {slices['development']['CSI800']['cagr']:.2%}",
              f"- 2022–2024 V0 / 中证800 CAGR：{slices['historical_observed_validation']['V0']['cagr']:.2%} / {slices['historical_observed_validation']['CSI800']['cagr']:.2%}",
              "", "## 阻断项",
              "- 财务公告日已做历史截断，但免费源不能证明数值为未修订原始版本，不能称为严格PIT。",
              "- 历史数据已经被观察，真样本外只能从冻结时间之后向前积累。",
              "- 前复权价格用于收益与100股手数近似，不等同逐次派息送转现金流账本。"]
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
