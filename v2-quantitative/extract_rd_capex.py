#!/usr/bin/env python3
"""
Extract R&D, CAPEX, and employee data from annual report PDFs.
Uses pdftotext to extract text, then regex to find relevant sections.
"""
from __future__ import annotations

import csv, json, re, subprocess, time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
DOCS_BASE = HERE.parent / "documents" / "cninfo"
COHORT = HERE.parent / "cohort" / "cohort-v1.csv"

# 45 hot companies
HOT_CODES = ["000036","000048","000636","000663","000791","000799","000810","000968","000975","001203","002032","002098","002128","002258","002304","002432","002460","002466","002468","002508","002558","002677","002739","002847","002963","300002","300080","300196","300246","300343","300410","300450","300461","300531","300586","300592","300618","300693","300735","300770","300785","300856","300896","300979","301004"]

# Patterns to search for
PATTERNS = {
    "研发投入": re.compile(r"研发投入.{0,80}?(\d[\d,.]*\s*万?元)", re.IGNORECASE),
    "研发费用": re.compile(r"(?:研发费用|研发支出).{0,80}?(\d[\d,.]*\s*万?元)", re.IGNORECASE),
    "研发占收入": re.compile(r"研发投入.{0,50}?(\d+\.?\d*)%", re.IGNORECASE),
    "资本开支": re.compile(r"(?:购建固定资产|资本开支|资本支出).{0,80}?(\d[\d,.]*\s*万?元)", re.IGNORECASE),
    "在建工程": re.compile(r"在建工程.{0,80}?(\d[\d,.]*\s*万?元)", re.IGNORECASE),
    "员工人数": re.compile(r"员工.{0,30}?(\d[\d,.]*)\s*人", re.IGNORECASE),
    "研发人员": re.compile(r"研发人员.{0,50}?(\d[\d,.]*)\s*人", re.IGNORECASE),
    "专利数量": re.compile(r"(?:专利|专利申请).{0,30}?(\d[\d,.]*)\s*(?:项|个)", re.IGNORECASE),
}

def extract_text(pdf_path: Path, timeout: int = 60) -> str:
    """Extract text from a PDF using pdftotext."""
    try:
        result = subprocess.run(["pdftotext", "-l", "150", str(pdf_path), "-"],
                              capture_output=True, text=True, timeout=timeout)
        return result.stdout
    except: return ""

def search_text(text: str) -> dict:
    """Search extracted text for all patterns."""
    results = {}
    for key, pattern in PATTERNS.items():
        matches = pattern.findall(text)
        if matches:
            results[key] = matches[0]
        else:
            results[key] = ""
    return results

def main():
    print(f"Scanning {len(HOT_CODES)} companies for annual report PDFs...")
    
    # Find PDFs
    pdfs = []
    for code in HOT_CODES:
        base = DOCS_BASE / code
        if not base.exists():
            continue
        for year_dir in sorted(base.iterdir()):
            if not year_dir.is_dir() or not year_dir.name.isdigit():
                continue
            for pdf in year_dir.glob("*.pdf"):
                pdfs.append((code, year_dir.name, pdf))
    
    print(f"Found {len(pdfs)} PDFs")
    
    # Process each PDF
    results = []
    processed = 0
    found_any = 0
    
    for code, year, pdf_path in pdfs:
        processed += 1
        if processed % 25 == 0:
            print(f"  Progress: {processed}/{len(pdfs)} ({found_any} with data)")
        
        text = extract_text(pdf_path)
        if not text or len(text) < 500:
            continue
        
        data = search_text(text)
        has_data = any(v for v in data.values())
        if has_data:
            found_any += 1
            results.append({
                "code": code,
                "year": year,
                "pdf": pdf_path.name,
                "text_len": len(text),
                **data
            })
    
    print(f"\nProcessed {processed} PDFs, {found_any} with R&D/CAPEX data")
    
    # Write results
    if results:
        rf = DATA / "rd_capex_data.csv"
        keys = ["code", "year", "pdf", "text_len"] + list(PATTERNS.keys())
        with open(rf, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            for r in results:
                w.writerow({k: r.get(k, "") for k in keys})
        print(f"Written: {rf} ({len(results)} rows)")
    
    # Summary
    by_code = defaultdict(int)
    for r in results:
        by_code[r["code"]] += 1
    
    print(f"\n=== Extraction Summary ===")
    print(f"Companies with data: {len(by_code)}")
    print(f"Total data points: {len(results)}")
    for key in PATTERNS:
        count = sum(1 for r in results if r.get(key))
        print(f"  {key}: {count} matches")
    
    summary = {"total_pdfs": len(pdfs), "processed": processed, "with_data": found_any,
               "companies": len(by_code), "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    (DATA / "rd_capex_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
