# V3 — Thematic Research System

主题驱动研究系统。基于产业政策、技术路线和竞争格局分析，寻找结构性增长赛道。

## 核心流程

1. `fetch_policy_data.py` — 采集国务院/工信部政策文件
2. 研究员按 TRACK-RESEARCH-FRAMEWORK.md 框架做深度分析
3. 输出赛道推荐 → 集中持有5-10只

## 关键文件

- `research/` — 研究框架和模板
- `src/` — 政策采集器
- `data/` — 研究输出

## 依赖

- Python 3.14+
- requests, beautifulsoup4（政策采集器需要）
- 共享数据：`../shared/data/`
