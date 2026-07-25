#!/usr/bin/env python3
"""Build company-year alpha evidence coverage and unbiased external-source requirements."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
INDUSTRY = HERE.parent
TOLL_FIELDS = [
    "effective_supplier_count",
    "certification_months",
    "capacity_lead_months",
    "exclusive_process_or_patent",
    "long_contract_years",
    "customer_prepayment",
    "gross_margin_5y",
]
COMPANY_FIELDS = [
    "single_customer_share",
    "ocf_to_profit",
    "rd_ratio",
    "gross_margin_5y",
    "strategic_focus",
    "capital_allocation",
]
TRACK_FIELDS = [
    "demand_irreversibility",
    "penetration_rate",
    "tam_current",
    "tam_5y",
    "policy_level",
]


def main() -> None:
    evidence = pd.read_csv(INDUSTRY / "evidence-candidates.csv", dtype={"code": str})
    evidence["code"] = evidence["code"].str.zfill(6)
    tiers = pd.read_csv(INDUSTRY / "archive" / "storage-tiers.csv", dtype={"code": str})
    hot = set(tiers[tiers["storage_tier"].eq("hot_keep")]["code"].str.zfill(6))
    cohort = pd.read_csv(INDUSTRY / "cohort" / "cohort-v1.csv", dtype={"code": str})
    cohort["code"] = cohort["code"].str.zfill(6)
    cohort = cohort[cohort["code"].isin(hot)]
    base = (
        evidence[["code", "report_year"]]
        .drop_duplicates()
        .merge(
            cohort[["code", "name_at_last_seen", "industry_at_last_seen", "cohort_role"]],
            on="code",
            how="left",
        )
    )
    candidate_sets = (
        evidence.groupby(["code", "report_year"])["metric_id"].agg(lambda values: set(values)).to_dict()
    )
    rows = []
    for item in base.itertuples():
        available = candidate_sets.get((item.code, item.report_year), set())
        rows.append(
            {
                "code": item.code,
                "name": item.name_at_last_seen,
                "industry_current_label": item.industry_at_last_seen,
                "report_year": item.report_year,
                "cohort_role": item.cohort_role,
                "toll_candidate_fields": len(set(TOLL_FIELDS) & available),
                "toll_required_fields": len(TOLL_FIELDS),
                "company_candidate_fields": len(set(COMPANY_FIELDS) & available),
                "company_required_fields": len(COMPANY_FIELDS),
                "track_verified_fields": 0,
                "track_required_fields": len(TRACK_FIELDS),
                "strict_alpha_ready": False,
                "status": "candidate_only_not_verified",
            }
        )
    coverage = pd.DataFrame(rows)
    coverage.to_csv(HERE / "data" / "alpha-company-year-coverage.csv", index=False)
    requirements = []
    for industry in sorted(cohort["industry_at_last_seen"].dropna().unique()):
        for year in range(2016, 2025):
            for metric in TRACK_FIELDS:
                requirements.append(
                    {
                        "industry_current_label": industry,
                        "decision_year": year,
                        "metric_id": metric,
                        "required_source_1": "government_or_association_primary",
                        "required_source_2": "independent_primary",
                        "status": "missing",
                        "historical_industry_mapping_required": True,
                        "warning": "current industry label is routing metadata only and cannot be backfilled into historical signals",
                    }
                )
    pd.DataFrame(requirements).to_csv(
        HERE / "data" / "track-source-requirements.csv", index=False
    )
    summary = {
        "company_year_rows": len(coverage),
        "companies": int(coverage["code"].nunique()),
        "industries_current_labels": int(cohort["industry_at_last_seen"].nunique()),
        "toll_full_candidate_rows": int(
            coverage["toll_candidate_fields"].eq(coverage["toll_required_fields"]).sum()
        ),
        "company_full_candidate_rows": int(
            coverage["company_candidate_fields"].eq(coverage["company_required_fields"]).sum()
        ),
        "strict_alpha_ready_rows": 0,
        "track_source_requirements": len(requirements),
        "status": "collection plan frozen; external primary sources missing",
    }
    (HERE / "data" / "alpha-coverage.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
