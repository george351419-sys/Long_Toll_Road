#!/usr/bin/env python3
"""仓位管理系统"""
from __future__ import annotations
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILE = HERE.parent / "research" / "data" / "positions.json"

def calculate_position(thesis_confidence: float, portfolio_volatility: float,
                       market_volatility: float, max_per_stock: float = 0.15) -> float:
    """
    基于论题置信度计算目标仓位。
    
    参数：
        thesis_confidence: 论题置信度 1-10
        portfolio_volatility: 组合波动率
        market_volatility: 市场波动率
        max_per_stock: 单只上限
    
    返回：
        目标仓位比例（0-1）
    """
    base = thesis_confidence / 10  # 0.1 to 1.0
    vol_adjust = min(portfolio_volatility / market_volatility, 2) if market_volatility > 0 else 1
    return min(base / vol_adjust, max_per_stock)

def rebalance_positions(theses: list[dict], total_capital: float) -> dict:
    """根据所有论题的置信度分配资金"""
    total_confidence = sum(t.get("confidence", 5) for t in theses)
    if total_confidence == 0: return {}
    
    allocations = {}
    for t in theses:
        pct = t["confidence"] / total_confidence
        allocations[t.get("company", t.get("industry"))] = {
            "pct": round(pct * 100, 1),
            "amount": round(total_capital * pct, 2),
            "confidence": t["confidence"]
        }
    return allocations

if __name__ == "__main__":
    print("Position manager loaded")
    print(f"Example: 7/10 confidence -> {calculate_position(7, 0.25, 0.25)*100:.0f}% position")
