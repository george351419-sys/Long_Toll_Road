#!/usr/bin/env python3
"""Build historical industry routing candidates without using current labels as backfill."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
INDUSTRY = HERE.parent
SNAPSHOTS = INDUSTRY.parent / "pit" / "data" / "financial_snapshots"


def main() -> None:
    cohort = pd.read_csv(INDUSTRY / "cohort" / "cohort-v1.csv", dtype={"code": str})
    cohort_codes = set(cohort.code.str.zfill(6))
    rows = []
    for path in sorted(SNAPSHOTS.glob("20*.csv")):
        frame = pd.read_csv(path, dtype={"SECURITY_CODE": str}, low_memory=False)
        frame["code"] = frame.SECURITY_CODE.str.zfill(6)
        keep = frame[frame.code.isin(cohort_codes)].copy()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        for item in keep.itertuples():
            report = str(item.REPORTDATE).replace("-", "")[:8]
            notice = str(item.NOTICE_DATE) if pd.notna(item.NOTICE_DATE) else None
            rows.append({
                "code": item.code,
                "report_period": report,
                "industry_label_at_snapshot": item.PUBLISHNAME if pd.notna(item.PUBLISHNAME) else "未知",
                "notice_date_candidate": notice,
                "snapshot_file": path.name,
                "snapshot_sha256": digest,
                "status": "vendor_snapshot_candidate_not_verified",
                "warning": "routing candidate only; must not be promoted to strict PIT industry fact without original source verification",
            })
    result = pd.DataFrame(rows).drop_duplicates(["code", "report_period"], keep="last")
    result.to_csv(HERE / "data" / "historical-industry-mapping-candidates.csv", index=False)
    summary = {
        "rows": len(result), "companies": int(result.code.nunique()),
        "report_periods": int(result.report_period.nunique()),
        "unknown_industry_rows": int(result.industry_label_at_snapshot.eq("未知").sum()),
        "status": "candidate_only_not_strict_PIT",
    }
    (HERE / "data" / "historical-industry-mapping-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
