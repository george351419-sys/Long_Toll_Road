#!/usr/bin/env python3
"""
Policy document fetcher. Searches government websites for industry-specific
policy documents and scores policy intensity by industry/year.

Sources: 国务院(gov.cn), 发改委(ndrc.gov.cn), 工信部(miit.gov.cn)
"""
from __future__ import annotations

import csv, json, re, time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = DATA / "policy_data.csv"

# Key industry keywords to track (top 30 industries from our dashboard)
INDUSTRY_KEYWORDS = [
    "新能源", "半导体", "医疗器械", "创新药", "人工智能", "光伏", "锂电池",
    "新能源汽车", "机器人", "高端制造", "集成电路", "生物医药", "数字经济",
    "量子计算", "航空航天", "新材料", "国防军工", "5G", "大数据", "云计算",
    "物联网", "智能汽车", "充电桩", "储能", "风电", "氢能", "节能环保",
    "工业互联网", "信创", "种业"
]

# Search URLs (using site search via Google/Bing as fallback)
SEARCH_SOURCES = [
    {"name": "国务院", "url_template": "https://www.gov.cn/search/index.html?q={keyword}"},
    {"name": "工信部", "url_template": "https://www.miit.gov.cn/search/index.html?q={keyword}"},
    {"name": "百度", "url_template": "https://www.baidu.com/s?wd={keyword}+site:gov.cn+政策"},
]

def search_url(url: str, timeout: int = 15) -> list[dict]:
    """Search a URL and extract document results."""
    try:
        resp = requests.get(url, timeout=timeout, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        
        results = []
        # Try common search result patterns
        for link in soup.find_all("a", href=True):
            href = link["href"]
            text = link.get_text(strip=True)
            if len(text) > 10 and ("政策" in text or "规划" in text or "意见" in text or "通知" in text):
                results.append({"title": text[:200], "url": href})
        
        return results[:20]  # Top 20 results
    except:
        return []

def score_document(title: str) -> dict:
    """Score a policy document by specificity and relevance."""
    has_quant_target = bool(re.search(r"\d+[万亿\%]", title))
    has_action_word = bool(re.search(r"(印发|实施|试行|发布|成立|设立)", title))
    has_directive = bool(re.search(r"(意见|通知|规划|纲要|方案|办法|条例)", title))
    
    score = 0
    if has_quant_target: score += 5  # Has concrete numbers
    if has_action_word: score += 3   # Has action verbs
    if has_directive: score += 2     # Is a formal directive
    
    return {"score": score, "has_quant": has_quant_target, "is_directive": has_directive}

def main():
    print(f"Scanning policies for {len(INDUSTRY_KEYWORDS)} industry keywords...")
    print(f"Sources: {len(SEARCH_SOURCES)} websites")
    
    all_results = []
    for keyword in INDUSTRY_KEYWORDS[:5]:  # First 5 for testing
        print(f"\n  Searching: {keyword}")
        for source in SEARCH_SOURCES:
            url = source["url_template"].format(keyword=quote_plus(keyword))
            docs = search_url(url)
            for doc in docs:
                scoring = score_document(doc["title"])
                all_results.append({
                    "keyword": keyword,
                    "source": source["name"],
                    "title": doc["title"],
                    "url": doc["url"],
                    "score": scoring["score"],
                    "has_quant": scoring["has_quant"],
                    "is_directive": scoring["is_directive"],
                })
            time.sleep(1)  # Rate limit
    
    # Write results
    if all_results:
        keys = ["keyword", "source", "title", "url", "score", "has_quant", "is_directive"]
        with open(OUT, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            for r in all_results:
                w.writerow({k: r.get(k, "") for k in keys})
        
        # Summary
        by_keyword = defaultdict(int)
        by_source = defaultdict(int)
        high_score = [r for r in all_results if r["score"] >= 5]
        
        for r in all_results:
            by_keyword[r["keyword"]] += 1
            by_source[r["source"]] += 1
        
        print(f"\n=== Policy Scan Results ===")
        print(f"Total documents: {len(all_results)}")
        print(f"High-impact documents: {len(high_score)}")
        print(f"By source: {dict(by_source)}")
        print(f"Top keywords:")
        for kw, count in sorted(by_keyword.items(), key=lambda x: -x[1])[:10]:
            print(f"  {kw}: {count} documents")
        print(f"\nWritten: {OUT}")
    else:
        print("\nNo results - network may be unavailable")

if __name__ == "__main__":
    main()
