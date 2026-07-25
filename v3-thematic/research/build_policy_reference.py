#!/usr/bin/env python3
"""
Build policy reference scores from known five-year plan priorities.
Uses 十三五(2016-2020) and 十四五(2021-2025) policy frameworks.
No network access needed.
"""
from __future__ import annotations

import json, csv
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
COHORT = HERE.parent / "cohort" / "cohort-v1.csv"

# Five Year Plan policy priorities
# 十三五 (2016-2020) key industries
POLICY_13_5 = {
    "新一代信息技术": ["半导体","集成电路","5G","人工智能","大数据","云计算","物联网"],
    "高端装备制造": ["机器人","航天装备Ⅱ","航空装备Ⅱ","自动化设备"],
    "新能源": ["光伏设备","风电","储能","新能源"],
    "新能源汽车": ["电池","能源金属","乘用车"],
    "生物医药": ["生物制品","化学制药","医疗器械","医疗服务"],
    "新材料": ["新材料","电子化学品Ⅱ","化工新材料"],
    "节能环保": ["节能环保","环保设备Ⅱ"],
}

# 十四五 (2021-2025) key industries  
POLICY_14_5 = {
    "科技自立自强": ["半导体","集成电路","人工智能","高端软件","信创"],
    "新能源革命": ["光伏设备","风电","储能","氢能","电池","能源金属"],
    "数字经济": ["5G","大数据","云计算","物联网","消费电子","计算机设备"],
    "健康中国": ["生物制品","医疗器械","创新药","医疗美容","医疗服务"],
    "消费升级": ["化妆品","休闲食品","白酒Ⅱ","调味发酵品Ⅱ","厨卫电器"],
    "国防现代化": ["军工电子Ⅱ","航天装备Ⅱ","航空装备Ⅱ","航海装备"],
}

# Also check known high-priority industries from recent policy documents
SPECIAL_DESIGNATIONS = {
    "集成电路": 10, "半导体": 9, "人工智能": 9, 
    "光伏设备": 9, "电池": 9, "新能源汽车": 9,
    "医疗器械": 8, "创新药": 8, "生物制品": 8,
    "机器人": 8, "军工电子Ⅱ": 8,
}

# Load all industries from cohort
all_inds = set()
with open(COHORT) as f:
    for r in csv.DictReader(f):
        all_inds.add(r.get("industry_at_last_seen", ""))

# Score each industry for each period
policy_scores = {}
for ind in sorted(all_inds):
    if not ind: continue
    
    # Score: 0-10, where 10 = highest policy priority
    score_13_5 = 0
    score_14_5 = 0
    
    # Check special designations
    if ind in SPECIAL_DESIGNATIONS:
        score_13_5 = max(score_13_5, SPECIAL_DESIGNATIONS[ind] - 1)  # Slightly lower in 13th
        score_14_5 = max(score_14_5, SPECIAL_DESIGNATIONS[ind])
    
    # Check five-year plan priorities
    for priority, industries in POLICY_13_5.items():
        if ind in industries:
            score_13_5 = max(score_13_5, 8 if priority == "新一代信息技术" else 7)
    
    for priority, industries in POLICY_14_5.items():
        if ind in industries:
            score_14_5 = max(score_14_5, 9 if priority in ("科技自立自强","新能源革命") else 8)
    
    policy_scores[ind] = {
        "score_2016_2020": score_13_5,
        "score_2021_2025": score_14_5,
        "max_score": max(score_13_5, score_14_5),
        "avg_score": (score_13_5 + score_14_5) / 2,
    }

# Write policy scores
with open(DATA / "policy_reference.json", "w") as f:
    json.dump(policy_scores, f, ensure_ascii=False, indent=2)

# Show results
print("=== 政策参考评分（基于十三五/十四五规划）===")
print(f"\n{'行业':25s} {'十三五':>6s} {'十四五':>6s} {'最高':>4s}")
print("-" * 45)
high = [(s["avg_score"], ind) for ind, s in policy_scores.items() if s["avg_score"] > 0]
high.sort(reverse=True)
for s, ind in high[:15]:
    d = policy_scores[ind]
    print(f"{ind:25s} {d['score_2016_2020']:>5.0f} {d['score_2021_2025']:>5.0f} {d['max_score']:>3.0f}")

print(f"\n零得分行业（五年规划未明确提及，需自行研究）:")
low = [(s["avg_score"], ind) for ind, s in policy_scores.items() if s["avg_score"] == 0]
print(f"  {len(low)}/{len(policy_scores)} 个行业")
for s, ind in low[:10]:
    print(f"  {ind}")
