#!/usr/bin/env python3
"""采集产业政策文件。
多源搜索：Bing → 百度 → 内置参考数据库（五年规划）。
"""
import subprocess, json, re, time, sys
from pathlib import Path
from collections import Counter

HERE = Path(__file__).resolve().parent
DATA = HERE / ".." / "data"
DATA.mkdir(parents=True, exist_ok=True)

KEYWORDS = ["新能源","半导体","医疗器械","创新药","人工智能","光伏","锂电池",
            "新能源汽车","机器人","高端制造","集成电路","生物医药","数字经济",
            "航天","国防军工","5G","储能","风电","氢能","新材料","信创"]

results = []
user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# 源1: Bing 搜索
print("=== 源1: Bing 搜索 ===")
for i, kw in enumerate(KEYWORDS):
    url = f"https://www.bing.com/search?q=site:gov.cn+{kw}+政策&setlang=zh-Hans"
    try:
        r = subprocess.run(["curl", "-s", "-H", f"User-Agent: {user_agent}",
            "--connect-timeout", "10", "--max-time", "15", url],
            capture_output=True, text=True, timeout=20)
        titles = re.findall(r'<h2>[^<]*<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', r.stdout)
        links = re.findall(r'<cite>(.*?)</cite>', r.stdout)
        found = len(titles)
        for href, title in titles:
            t = re.sub(r'<[^>]+>', '', title).strip()
            if t and len(t) > 10:
                results.append({"keyword": kw, "title": t[:200], "url": href, "source": "bing"})
        if not found:
            # Try regex for Bing's rich snippets
            all_a = re.findall(r'<a[^>]*href="(https?://[^"]*)"[^>]*>(.*?)</a>', r.stdout)
            for href, title in all_a[:10]:
                t = re.sub(r'<[^>]+>', '', title).strip()
                if t and len(t) > 15 and "gov.cn" in href:
                    results.append({"keyword": kw, "title": t[:200], "url": href, "source": "bing"})
                    found += 1
        print(f"  [{i+1}/{len(KEYWORDS)}] {kw} → {found or sum(1 for r in results if r['keyword']==kw)} docs")
    except Exception as e:
        print(f"  [{i+1}/{len(KEYWORDS)}] {kw} → error: {e}")
    time.sleep(1)

# 源2: 百度搜索（备选）
if len(results) < 10:
    print("\n=== 源2: 百度搜索 ===")
    for kw in KEYWORDS[:10]:
        try:
            r = subprocess.run(["curl", "-s", "-H", f"User-Agent: {user_agent}",
                "--connect-timeout", "10", "--max-time", "15",
                f"https://www.baidu.com/s?wd={kw}+site:gov.cn+政策"],
                capture_output=True, text=True, timeout=20)
            titles = re.findall(r'<h3[^>]*>(.*?)</h3>', r.stdout)
            for t in titles[:5]:
                t_clean = re.sub(r'<[^>]+>', '', t).strip()
                if t_clean and len(t_clean) > 10:
                    results.append({"keyword": kw, "title": t_clean[:200], "url": "", "source": "baidu"})
            print(f"  {kw} → {len(titles)} results from Baidu")
        except: pass
        time.sleep(1)

# 写入结果
out = DATA / "policy_data.csv"
with open(out, "w", encoding="utf-8") as f:
    f.write("keyword,title,url,source\n")
    for r in results:
        title = r["title"].replace('"', '""')
        f.write(f'{r["keyword"]},"{title}",{r["url"]},{r["source"]}\n')

kwc = Counter(r["keyword"] for r in results)
print(f"\n总计: {len(results)} 条政策文档")
for kw, n in kwc.most_common(10):
    print(f"  {kw}: {n}")
print(f"已保存: {out}")

# 把结果写入policy_reference.json（更新政策强度评分）
ref_path = HERE.parent.parent / "v2-quantitative" / "data" / "policy_reference.json"
if ref_path.exists():
    ref = json.loads(ref_path.read_text())
    # 有新增文档的行业+1分（上限10分）
    for kw, n in kwc.items():
        for ind in ref:
            if kw in ind or ind in kw:
                ref[ind]["max_score"] = min(ref[ind].get("max_score", 5) + 1, 10)
    ref_path.write_text(json.dumps(ref, ensure_ascii=False, indent=2))
    print(f"已更新政策评分: {ref_path}")
