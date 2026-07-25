#!/usr/bin/env python3
"""Audit the frozen 500-company cohort and produce a bounded document completion queue."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
INDUSTRY = HERE.parent


def main() -> None:
    cohort = pd.read_csv(INDUSTRY / "cohort" / "cohort-v1.csv", dtype={"code": str})
    coverage = pd.read_csv(INDUSTRY / "archive" / "cohort-document-coverage.csv", dtype={"code": str})
    queue = pd.read_csv(INDUSTRY / "archive" / "download-queue.csv", dtype={"code": str})
    cohort["code"] = cohort.code.str.zfill(6)
    coverage["code"] = coverage.code.str.zfill(6)
    queue["code"] = queue.code.str.zfill(6)
    summary = cohort.merge(coverage, on=["code", "cohort_role"], how="left")
    summary["annual"] = summary.annual.fillna(0).astype(int)
    summary["semiannual"] = summary.semiannual.fillna(0).astype(int)
    summary["document_coverage_status"] = summary.apply(
        lambda row: "no_indexed_documents" if row.annual == 0 and row.semiannual == 0
        else "partial_archive" if row.annual < 9 or row.semiannual < 9 else "historical_document_complete",
        axis=1,
    )
    summary.to_csv(HERE / "data" / "full-universe-coverage.csv", index=False)
    pending = queue[
        queue.code.isin(set(cohort.code)) & queue.download_status.eq("pending")
    ].copy()
    pending["priority"] = pending.category.map({"annual": 1, "semiannual": 2}).fillna(3)
    pending = pending.sort_values(["priority", "report_year", "code", "published_at"])
    pending.to_csv(HERE / "data" / "full-universe-stream-queue.csv", index=False)
    result = {
        "cohort_companies": len(cohort),
        "failure_or_delist": int(cohort.cohort_role.eq("failure_or_delist").sum()),
        "controls": int(cohort.cohort_role.eq("deterministic_control").sum()),
        "companies_with_no_indexed_documents": int(summary.document_coverage_status.eq("no_indexed_documents").sum()),
        "companies_with_partial_documents": int(summary.document_coverage_status.eq("partial_archive").sum()),
        "companies_with_9y_annual_and_semiannual": int(summary.document_coverage_status.eq("historical_document_complete").sum()),
        "pending_stream_documents": len(pending),
        "status": "universe frozen; queue is a collection plan, not a permission to backtest",
    }
    (HERE / "data" / "full-universe-audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
