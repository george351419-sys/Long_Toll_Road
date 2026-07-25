#!/usr/bin/env python3
"""
Fast toll data scanner: searches existing extracted text files (extracted-quarterly
and extracted-events) for toll-relevant patterns. Much faster than extracting from
PDFs since text is already extracted.

Patterns searched:
  - 核心竞争力 (core competitiveness) → patents, exclusive processes
  - 主要供应商/客户 (suppliers/customers) → supplier count, concentration
  - 产能 (capacity) → production capacity, expansion
  - 重要合同 (contracts) → long-term agreements
  - 毛利率 (gross margin) → margin stability
  - 专利/研发 (patent/R&D) → innovation moat
"""
from __future__ import annotations

import csv
import gzip
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
EXTRACTED_Q = HERE.parent / "extracted-quarterly"
EXTRACTED_E = HERE.parent / "extracted-events"

HOT_CODES = [
    "000036","000048","000636","000663","000791","000799","000810",
    "000968","000975","001203","002032","002098","002128","002258",
    "002304","002432","002460","002466","002468","002508","002558",
    "002677","002739","002847","002963","300002","300080","300196",
    "300246","300343","300410","300450","300461","300531","300586",
    "300592","300618","300693","300735","300770","300785","300856",
    "300896","300979","301004",
]

# Toll-relevant sections
SECTION_PATTERNS = {
    "核心竞争力": re.compile(
        r"核心竞争力分析.{0,500}?(?:研发|专利|商标|技术|创新)", re.DOTALL
    ),
    "主要客户": re.compile(
        r"主要(?:客户|销售客户).{0,200}?(\d+\.?\d*)%", re.DOTALL
    ),
    "主要供应商": re.compile(
        r"主要(?:供应商|采购商).{0,200}?(\d+\.?\d*)%", re.DOTALL
    ),
    "产能": re.compile(
        r"(?:设计产能|年产能|产能.*?[万吨GW台套只]|投产|达产)", re.IGNORECASE
    ),
    "合同": re.compile(
        r"重大合同|重要合同|长期(?:供应|销售|采购)合同|框架协议", re.IGNORECASE
    ),
    "毛利率": re.compile(
        r"毛利率.{0,100}?(\d+\.?\d*)%", re.DOTALL
    ),
    "专利": re.compile(
        r"专利[：:]\s*(\d+)", re.IGNORECASE
    ),
    "研发投入": re.compile(
        r"研发投入.{0,100}?(\d[\d,.]*\s*万元)", re.DOTALL
    ),
    "认证": re.compile(
        r"(?:ISO|GMP|FDA|CE|CCC|UL|认证|资质|许可)", re.IGNORECASE
    ),
}


def find_text_files() -> list[Path]:
    """Find all extracted text gzip files for hot companies."""
    files = []
    for base in [EXTRACTED_Q, EXTRACTED_E]:
        if not base.exists():
            continue
        for code in HOT_CODES:
            cdir = base / code
            if cdir.exists():
                files.extend(sorted(cdir.rglob("*.txt.gz")))
    return files


def read_text(path: Path) -> str:
    """Read text from a gzip file."""
    try:
        return gzip.open(path, "rt", errors="replace").read()
    except Exception:
        return ""


def search_toll(text: str, code: str, year: str) -> dict:
    """Search text for toll-relevant data."""
    result = {
        "code": code,
        "year": year,
        "found_core_competitiveness": False,
        "found_suppliers": False,
        "found_capacity": False,
        "found_contract": False,
        "patent_count": "",
        "gross_margin_pcts": [],
        "supplier_concentration": "",
        "customer_concentration": "",
        "rd_expense": "",
        "certifications": [],
        "text_len": len(text),
    }

    if not text:
        return result

    # Search sections
    for section, pattern in SECTION_PATTERNS.items():
        matches = pattern.findall(text)
        if matches:
            if section == "核心竞争力":
                result["found_core_competitiveness"] = True
            elif section == "主要客户":
                result["customer_concentration"] = str(matches[0])
            elif section == "主要供应商":
                result["found_suppliers"] = True
                if isinstance(matches[0], str):
                    result["supplier_concentration"] = matches[0]
            elif section == "产能":
                result["found_capacity"] = True
            elif section == "合同":
                result["found_contract"] = True
            elif section == "毛利率":
                result["gross_margin_pcts"] = [str(m) for m in matches[:5]]
            elif section == "专利":
                result["patent_count"] = str(matches[0])
            elif section == "研发投入":
                if isinstance(matches[0], str):
                    result["rd_expense"] = matches[0]
            elif section == "认证":
                result["certifications"] = list(set(matches[:10]))

    return result


def main():
    files = find_text_files()
    print(f"Found {len(files)} extracted text files")
    
    if not files:
        print("No text files found!")
        return
    
    # Process files
    results = []
    processed = 0
    by_code_year = defaultdict(int)
    
    for f in files:
        processed += 1
        if processed % 200 == 0:
            print(f"  Progress: {processed}/{len(files)}")
        
        # Extract code and year from path
        parts = f.parts
        code = parts[-3]
        year_str = parts[-2]
        
        text = read_text(f)
        if not text:
            continue
        
        data = search_toll(text, code, year_str)
        data["file_path"] = str(f)
        
        # Deduplicate: keep one entry per (code, year)
        key = (code, year_str)
        if key not in by_code_year:
            by_code_year[key] = len(results)
            results.append(data)
        else:
            # Merge: combine data from multiple files for same (code, year)
            idx = by_code_year[key]
            existing = results[idx]
            if data["found_core_competitiveness"]:
                existing["found_core_competitiveness"] = True
            if data["found_suppliers"]:
                existing["found_suppliers"] = True
            if data["found_capacity"]:
                existing["found_capacity"] = True
            if data["found_contract"]:
                existing["found_contract"] = True
            if data["patent_count"] and not existing["patent_count"]:
                existing["patent_count"] = data["patent_count"]
            if data["supplier_concentration"]:
                existing["supplier_concentration"] = data["supplier_concentration"]
            if data["customer_concentration"]:
                existing["customer_concentration"] = data["customer_concentration"]
            if data["gross_margin_pcts"]:
                existing["gross_margin_pcts"] = list(
                    set(existing["gross_margin_pcts"] + data["gross_margin_pcts"])
                )
            if data["rd_expense"]:
                existing["rd_expense"] = data["rd_expense"]
            if data["certifications"]:
                existing["certifications"] = list(
                    set(existing["certifications"] + data["certifications"])
                )

    print(f"\nUnique (code, year) entries: {len(results)}")

    # Write results
    rf = DATA / "toll-candidates-fast.csv"
    if results:
        # Flatten lists for CSV
        for r in results:
            r["certification_count"] = len(r.get("certifications", []))
            r["gross_margin_std"] = ""
            if r.get("gross_margin_pcts"):
                try:
                    vals = [float(v) for v in r["gross_margin_pcts"] if v]
                    if len(vals) > 1:
                        import statistics
                        r["gross_margin_std"] = round(float(statistics.stdev(vals)), 2)
                        r["gross_margin_mean"] = round(float(statistics.mean(vals)), 2)
                except (ValueError, statistics.StatisticsError):
                    pass
        
        with open(rf, "w", newline="") as f:
            keys = [
                "code", "year", "found_core_competitiveness",
                "found_suppliers", "found_capacity", "found_contract",
                "patent_count", "supplier_concentration",
                "customer_concentration", "gross_margin_mean",
                "gross_margin_std", "rd_expense", "certification_count",
                "text_len", "file_path",
            ]
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            for r in results:
                row = {k: r.get(k, "") for k in keys}
                for k in keys:
                    if isinstance(row[k], bool):
                        row[k] = "True" if row[k] else "False"
                    elif row[k] is None:
                        row[k] = ""
                w.writerow(row)
        print(f"Written: {rf} ({len(results)} rows)")

    # Summary
    companies = set(r["code"] for r in results)
    with_core = sum(1 for r in results if r.get("found_core_competitiveness"))
    with_suppliers = sum(1 for r in results if r.get("found_suppliers"))
    with_capacity = sum(1 for r in results if r.get("found_capacity"))
    with_contracts = sum(1 for r in results if r.get("found_contract"))
    with_patents = sum(1 for r in results if r.get("patent_count"))
    years = [r["year"] for r in results if r["year"].isdigit()]
    
    print(f"\n=== Toll Data Scan Summary ===")
    if years:
        print(f"Years: {min(years)} - {max(years)}")
    print(f"Companies: {len(companies)}")
    print(f"Total (code, year) entries: {len(results)}")
    print(f"With core competitiveness:          {with_core}")
    print(f"With supplier data:                {with_suppliers}")
    print(f"With capacity data:                {with_capacity}")
    print(f"With contract data:                {with_contracts}")
    print(f"With patent count:                 {with_patents}")

    summary = {
        "total_entries": len(results),
        "companies": len(companies),
        "with_core_competitiveness": with_core,
        "with_suppliers": with_suppliers,
        "with_capacity": with_capacity,
        "with_contracts": with_contracts,
        "with_patent_count": with_patents,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (DATA / "toll-candidates-fast-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
