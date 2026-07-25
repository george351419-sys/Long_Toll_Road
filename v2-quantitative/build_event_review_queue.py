#!/usr/bin/env python3
"""Reduce page snippets to one human-review record per announcement and event type."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
HARD = {"investigation", "administrative_penalty", "audit_opinion", "listing_risk"}


def disposition(metric: str, title: str) -> str:
    if metric != "listing_risk":
        return "review_hard_veto_scope"
    if re.search(r"撤销|恢复上市|摘帽", title):
        return "review_hard_veto_reversal"
    if re.search(r"实施|实行|继续实行|暂停上市|终止上市|退市整理", title):
        return "review_hard_veto_onset"
    return "review_listing_risk_scope"


def main() -> None:
    candidates = pd.read_csv(
        HERE / "data" / "event-evidence-candidates.csv",
        dtype={"code": str, "announcement_id": str},
    )
    queue = pd.read_csv(
        HERE / "data" / "priority-event-evidence-queue.csv",
        dtype={"code": str, "announcement_id": str},
    )
    # Earliest page is the review anchor; all pages remain in the candidate ledger.
    rows = (
        candidates.sort_values(["announcement_id", "metric_id", "page"])
        .drop_duplicates(["announcement_id", "metric_id"], keep="first")
        .merge(
            queue[["announcement_id", "title", "pdf_url", "text_path"]],
            on="announcement_id",
            how="left",
        )
    )
    rows["review_disposition"] = rows.apply(
        lambda row: disposition(row.metric_id, row.title), axis=1
    )
    rows["review_priority"] = rows["metric_id"].map(
        lambda metric: 1 if metric in HARD else 2
    )
    rows["review_status"] = "pending_human_or_visual_review"
    rows = rows.sort_values(["review_priority", "published_at", "code"])
    rows.to_csv(HERE / "data" / "event-review-queue.csv", index=False)
    summary = {
        "review_rows": len(rows),
        "unique_announcements": int(rows["announcement_id"].nunique()),
        "hard_veto_review_rows": int(rows["metric_id"].isin(HARD).sum()),
        "hard_veto_onset_rows": int(rows["review_disposition"].eq("review_hard_veto_onset").sum()),
        "hard_veto_reversal_rows": int(rows["review_disposition"].eq("review_hard_veto_reversal").sum()),
        "alpha_review_rows": int((~rows["metric_id"].isin(HARD)).sum()),
        "status": "review queue only; no event promoted to verified",
    }
    (HERE / "data" / "event-review-queue.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
