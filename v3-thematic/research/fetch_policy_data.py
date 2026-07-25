#!/usr/bin/env python3
"""Fetch policy documents from gov.cn search API via curl."""
import subprocess, json, time, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / ".." / "data"
DATA.mkdir(parents=True, exist_ok=True)

KEYWORDS = ["新能源","半导体","医疗器械","创新药","人工智能","光伏","锂电池","新能源汽车","机器人","高端制造",
            "集成电路","生物医药","数字经济","航天","国防军工","5G","储能","风电","氢能","新材料",
            "量子计算","大数据","云计算","物联网","信创","种业","工业互联网"]

results = []
for i, kw in enumerate(KEYWORDS):
    print(f"[{i+1}/{len(KEYWORDS)}] {kw}", end=" ", flush=True)
    try:
        r = subprocess.run(["curl", "-s", "--connect-timeout", "10", "--max-time", "15",
            f"https://www.google.com/search?q=site:gov.cn+{kw}+政策&hl=zh-CN"],
            capture_output=True, text=True, timeout=20)
        # Extract titles and links with regex
        titles = re.findall(r'<h3[^>]*>(.*?)</h3>', r.stdout, re.DOTALL)
        links = re.findall(r'<a[^>]*href="(/url\?q=[^"&]+)', r.stdout)
        found = 0
        for t, l in zip(titles, links):
            url = l.split("q=")[1].split("&")[0] if "/url?q=" in l else l
            title = re.sub(r'<[^>]+>', '', t).strip()
            if title and len(title) > 10:
                results.append({"keyword": kw, "title": title[:200], "url": url, "source": "google_gov"})
                found += 1
        print(f"→ {found} docs")
    except Exception as e:
        print(f"→ error: {e}")
    time.sleep(1)

# Fallback: try gov.cn search directly
if len(results) < 10:
    print("\nTrying gov.cn direct search...")
    for kw in KEYWORDS[:5]:
        try:
            r = subprocess.run(["curl", "-s", "--connect-timeout", "10", "--max-time", "15",
                f"https://www.gov.cn/search/index.html?q={kw}"],
                capture_output=True, text=True, timeout=20)
            # Look for JSON data in the page
            json_match = re.search(r'({.*"total".*})', r.stdout)
            if json_match:
                data = json.loads(json_match.group(1))
                for doc in data.get("data", {}).get("list", []):
                    results.append({"keyword": kw, "title": doc.get("title","")[:200],
                                    "url": doc.get("url",""), "source": "gov_cn"})
            print(f"  {kw}: {len(results)} total")
        except: pass
        time.sleep(1)

# Write results
out = DATA / "policy_data.csv"
with open(out, "w", encoding="utf-8") as f:
    f.write("keyword,title,url,source\n")
    for r in results:
        title = r["title"].replace('"', '""')
        f.write(f'{r["keyword"]},"{title}",{r["url"]},{r["source"]}\n')

from collections import Counter
kwc = Counter(r["keyword"] for r in results)
print(f"\nTotal: {len(results)} documents")
for kw, n in kwc.most_common(10):
    print(f"  {kw}: {n}")
print(f"Saved: {out}")
