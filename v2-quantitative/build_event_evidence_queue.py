#!/usr/bin/env python3
"""Deduplicate and classify CNINFO event hits under frozen title-only rules."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent


def clean(value: object) -> str:
    return re.sub(r"<[^>]+>", "", str(value or "")).strip()


def main() -> None:
    rules = json.loads(
        (HERE / "event-classification-rules.json").read_text(encoding="utf-8")
    )
    source = pd.read_csv(
        HERE / "data" / "priority-event-index.csv",
        dtype={"code": str, "announcement_id": str},
    )
    source["code"] = source["code"].str.zfill(6)
    source["clean_title"] = source["title"].map(clean)
    source["clean_name"] = source["name"].map(clean)
    grouped = (
        source.sort_values(["announcement_id", "keyword"])
        .groupby("announcement_id", as_index=False)
        .agg(
            code=("code", "first"),
            name=("clean_name", "first"),
            title=("clean_title", "first"),
            published_at=("published_at", "first"),
            org_id=("org_id", "first"),
            adjunct_url=("adjunct_url", "first"),
            pdf_url=("pdf_url", "first"),
            matched_keywords=("keyword", lambda values: ";".join(sorted(set(values)))),
        )
    )
    hard = set(rules["hard_veto_candidate_keywords"])
    alpha = set(rules["alpha_candidate_keywords"])

    def classify(row: pd.Series) -> str:
        keywords = set(row.matched_keywords.split(";"))
        if keywords & hard:
            return "hard_veto_candidate"
        if "ST" in keywords and re.search(rules["st_title_include_regex"], row.title):
            if re.search(rules["st_direction"]["reversal_regex"], row.title):
                return "hard_veto_reversal_candidate"
            return "hard_veto_candidate"
        if keywords & alpha:
            return "alpha_evidence_candidate"
        return "index_only_noise"

    grouped["candidate_class"] = grouped.apply(classify, axis=1)
    grouped["cancelled_title"] = grouped["title"].str.contains(
        rules["title_flags"]["cancelled_regex"], regex=True
    )
    grouped["revision_title"] = grouped["title"].str.contains(
        rules["title_flags"]["revision_regex"], regex=True
    )
    grouped["archive_status"] = grouped["candidate_class"].map(
        lambda value: "pending" if value != "index_only_noise" else "not_selected"
    )
    grouped["pdf_sha256"] = ""
    grouped["pdf_bytes"] = 0
    grouped["text_sha256"] = ""
    grouped["compressed_text_bytes"] = 0
    grouped["text_path"] = grouped.apply(
        lambda row: (
            f"../extracted-events/{row.code}/{row.published_at[:4]}/"
            f"{row.announcement_id}.txt.gz"
        ),
        axis=1,
    )
    grouped.to_csv(HERE / "data" / "priority-event-evidence-queue.csv", index=False)
    selected = grouped[grouped["archive_status"].eq("pending")]
    summary = {
        "unique_index_announcements": len(grouped),
        "selected_for_stream_extraction": len(selected),
        "not_selected_title_noise": int(grouped["archive_status"].eq("not_selected").sum()),
        "classes": grouped["candidate_class"].value_counts().to_dict(),
        "cancelled_title_candidates": int(selected["cancelled_title"].sum()),
        "revision_title_candidates": int(selected["revision_title"].sum()),
        "status": "title_classified_candidate_only",
    }
    (HERE / "data" / "priority-event-evidence-queue.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
