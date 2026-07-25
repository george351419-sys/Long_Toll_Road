# V2 — Quantitative Factor System

自动化定量因子选股系统。基于财务数据、行业评分和回测引擎，每两年自动换仓。

## 核心流程

1. `build_industry_dashboard.py` — 更新129行业×7维评分
2. `build_composite_score.py` — 计算个股复合评分
3. `run_backtest_final.py` — 运行回测验证
4. 自动选前15名 → 等权持有2年

## 关键文件

- `engine/` — 回测引擎（`.py`）
- `scoring/` — 评分构建器（`.py`）
- `data/` — 行业仪表盘、选股CSV、策略配置
- `output/backtest/` — 回测结果（`.json`）

## 依赖

- Python 3.14+
- pandas, numpy, sqlite3
- 共享数据：`../shared/data/`
