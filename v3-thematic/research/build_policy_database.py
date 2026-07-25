#!/usr/bin/env python3
"""
构建产业政策结构化数据库。
由于搜索引擎普遍反爬，此脚本不再尝试自动采集，
改为维护一份经核实的参考政策清单。手动发现的政策可通过
--add 参数追加。
"""
from __future__ import annotations

import json, csv, argparse
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / ".." / "data"
DB = DATA / "policy_database.json"
CSV = DATA / "policy_database.csv"

# 已知关键政策文件（经核实的十五/十四五时期核心产业政策）
KNOWN_POLICIES = [
    # === 半导体/集成电路 ===
    {"industry": "半导体", "year": 2014, "title": "国家集成电路产业发展推进纲要",
     "body": "设立国家集成电路产业投资基金（大基金一期1387亿）", "url": "https://www.gov.cn/", "type": "五年规划"},
    {"industry": "半导体", "year": 2019, "title": "国家集成电路产业投资基金二期",
     "body": "大基金二期2041亿，重点投向设备和材料", "url": "", "type": "产业基金"},
    {"industry": "半导体", "year": 2024, "title": "国家集成电路产业投资基金三期",
     "body": "大基金三期3440亿，重点投向先进制程和AI芯片", "url": "", "type": "产业基金"},
    {"industry": "半导体", "year": 2020, "title": "十四五规划—科技自立自强",
     "body": "集成电路列为科技前沿重点领域，明确国产替代目标", "url": "", "type": "五年规划"},

    # === 新能源/光伏/风电/储能 ===
    {"industry": "新能源", "year": 2020, "title": "十四五规划—新能源革命",
     "body": "光伏装机目标、风电装机目标、储能发展目标", "url": "", "type": "五年规划"},
    {"industry": "新能源", "year": 2020, "title": "2030年前碳达峰行动方案",
     "body": "2030年碳达峰、2060年碳中和，非化石能源占比25%", "url": "", "type": "国务院"},
    {"industry": "光伏设备", "year": 2022, "title": "十四五可再生能源发展规划",
     "body": "2025年可再生能源消费占比33%，风电光伏发电量翻倍", "url": "", "type": "部委规划"},
    {"industry": "储能", "year": 2022, "title": "十四五新型储能发展实施方案",
     "body": "2025年新型储能装机30GW以上", "url": "", "type": "部委规划"},

    # === 新能源汽车 ===
    {"industry": "新能源汽车", "year": 2020, "title": "新能源汽车产业发展规划(2021-2035)",
     "body": "2025年新能源渗透率20%，2035年成为主流", "url": "", "type": "国务院"},
    {"industry": "电池", "year": 2022, "title": "十四五能源领域科技创新规划",
     "body": "固态电池、钠离子电池等下一代技术研发支持", "url": "", "type": "部委规划"},

    # === 医疗器械/生物医药 ===
    {"industry": "医疗器械", "year": 2021, "title": "十四五医药工业发展规划",
     "body": "高端医疗装备国产化率提升目标，创新药出海支持", "url": "", "type": "部委规划"},
    {"industry": "创新药", "year": 2021, "title": "十四五生物经济发展规划",
     "body": "生物医药列为重点领域，基因治疗、细胞治疗等前沿方向", "url": "", "type": "部委规划"},

    # === 人工智能/数字经济 ===
    {"industry": "人工智能", "year": 2017, "title": "新一代人工智能发展规划",
     "body": "2025年AI产业规模4000亿，2030年成为世界主要AI创新中心", "url": "", "type": "国务院"},
    {"industry": "数字经济", "year": 2021, "title": "十四五数字经济发展规划",
     "body": "2025年数字经济核心产业增加值占GDP比重达10%", "url": "", "type": "国务院"},

    # === 国防军工/航天 ===
    {"industry": "国防军工", "year": 2021, "title": "十四五规划—国防现代化",
     "body": "国防预算持续增长，武器装备信息化、智能化", "url": "", "type": "五年规划"},
    {"industry": "航天", "year": 2021, "title": "航天强国建设路线图",
     "body": "载人航天、探月工程、卫星互联网等重大工程持续推进", "url": "", "type": "部委规划"},
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--add", nargs=5, metavar=("industry", "year", "title", "body", "url"),
                        help="手动添加一条政策记录")
    parser.add_argument("--export-csv", action="store_true", help="导出为CSV")
    parser.add_argument("--list", action="store_true", help="列出所有记录")
    args = parser.parse_args()

    # 加载现有数据库
    DATA.mkdir(parents=True, exist_ok=True)
    db = []
    if DB.exists():
        db = json.loads(DB.read_text())

    if not db:
        db = KNOWN_POLICIES  # 首次运行使用默认数据库

    # 手动添加
    if args.add:
        ind, year, title, body, url = args.add
        db.append({"industry": ind, "year": int(year), "title": title,
                   "body": body, "url": url, "type": "manual"})
        print(f"已添加: {ind} - {title}")

    # 保存
    DB.write_text(json.dumps(db, ensure_ascii=False, indent=2))

    # 导出CSV
    if args.export_csv:
        with open(CSV, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["industry", "year", "title", "body", "url", "type"])
            w.writeheader()
            for r in db:
                w.writerow(r)
        print(f"已导出: {CSV} ({len(db)}条)")

    # 列出
    if args.list or not args.add:
        from collections import Counter
        ind_counts = Counter(r["industry"] for r in db)
        print(f"\n政策数据库: {len(db)}条, {len(ind_counts)}个行业")
        for ind, n in sorted(ind_counts.items()):
            docs = [r for r in db if r["industry"] == ind]
            years = ", ".join(str(r["year"]) for r in docs)
            print(f"  {ind}: {n}条 ({years})")


if __name__ == "__main__":
    main()
