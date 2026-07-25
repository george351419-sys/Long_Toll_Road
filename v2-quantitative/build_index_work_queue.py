#!/usr/bin/env python3
"""Build complete, resumable index-only work ledgers for track and event evidence."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
INDUSTRY = HERE.parent
TRACK_TERMS = {
    "demand_irreversibility": "需求量 保有量 渗透率 政策目标 强制标准",
    "penetration_rate": "渗透率 普及率 装机率 国产化率",
    "tam_current": "市场规模 产量 销量 收入 货运量",
    "tam_5y": "市场规模 预测 规划目标 复合增长率",
    "policy_level": "规划 意见 标准 目录 规范 政策",
}
EVENT_TERMS = [
    "重大合同",
    "中标",
    "客户认证",
    "合格供应商",
    "扩产",
    "投产",
    "立案",
    "行政处罚",
    "非标准审计意见",
    "ST",
    "退市",
]


def main() -> None:
    coverage = pd.read_csv(HERE / "data" / "track-source-coverage.csv")
    track = coverage[
        [
            "industry_current_label",
            "decision_year",
            "metric_id",
            "indexed_sources",
            "source_classes",
        ]
    ].copy()
    track["query_terms"] = track["metric_id"].map(TRACK_TERMS)
    track["required_domains"] = "gov.cn;部委官网;行业协会官网;统计公报"
    track["collection_mode"] = "index_first_stream_extract_delete_pdf"
    track["status"] = track.apply(
        lambda row: (
            "two_classes_indexed_not_verified"
            if row.source_classes >= 2
            else "one_class_indexed_not_verified"
            if row.indexed_sources >= 1
            else "index_missing"
        ),
        axis=1,
    )
    track.to_csv(HERE / "data" / "track-index-work-queue.csv", index=False)

    tiers = pd.read_csv(INDUSTRY / "archive" / "storage-tiers.csv", dtype={"code": str})
    companies = (
        tiers[tiers["storage_tier"].eq("hot_keep")][["code", "name"]]
        .drop_duplicates("code")
        .assign(code=lambda frame: frame["code"].str.zfill(6))
    )
    event = companies.merge(pd.DataFrame({"keyword": EVENT_TERMS}), how="cross")
    event["start"] = "2016-01-01"
    event["end"] = "2024-12-31"
    event["source"] = "巨潮资讯"
    event["status"] = "query_pending"
    event["collection_mode"] = "index_only"
    event.to_csv(HERE / "data" / "priority-event-query-ledger.csv", index=False)

    summary = {
        "track_index_tasks": len(track),
        "track_industries": int(track["industry_current_label"].nunique()),
        "track_years": [int(track.decision_year.min()), int(track.decision_year.max())],
        "track_missing": int(track["status"].eq("index_missing").sum()),
        "track_one_class": int(track["status"].eq("one_class_indexed_not_verified").sum()),
        "track_two_classes": int(track["status"].eq("two_classes_indexed_not_verified").sum()),
        "event_queries": len(event),
        "event_companies": int(event["code"].nunique()),
        "event_keywords": len(EVENT_TERMS),
        "event_status": "query_pending",
        "storage_policy": "save indexes and extracted text only; delete each temporary PDF",
    }
    (HERE / "data" / "index-work-queue-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
