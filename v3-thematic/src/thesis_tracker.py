#!/usr/bin/env python3
"""论题追踪系统"""
from __future__ import annotations
import json, csv
from collections import defaultdict
from pathlib import Path
from datetime import datetime

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "research" / "data"

class Thesis:
    def __init__(self, industry: str, company: str = ""):
        self.industry = industry
        self.company = company
        self.created_at = datetime.now().isoformat()
        self.core_thesis = ""
        self.confidence = 5  # 1-10
        self.position_size = 0  # 目标仓位百分比
        self.status = "researching"  # researching/active/exited/cancelled
        self.validation_signals = []  # 论题成立的验证信号
        self.invalidation_signals = []  # 证伪条件
        self.last_review = ""
        self.notes = []

    def to_dict(self):
        return {"industry": self.industry, "company": self.company,
                "created_at": self.created_at, "core_thesis": self.core_thesis,
                "confidence": self.confidence, "position_size": self.position_size,
                "status": self.status, "validation_signals": self.validation_signals,
                "invalidation_signals": self.invalidation_signals,
                "last_review": self.last_review, "notes": self.notes}

class ThesisManager:
    def __init__(self):
        self.theses: list[Thesis] = []
        self.file = DATA / "theses.json"
        if self.file.exists():
            self.load()

    def add(self, thesis: Thesis):
        self.theses.append(thesis)
        self.save()

    def save(self):
        DATA.mkdir(parents=True, exist_ok=True)
        with open(self.file, "w") as f:
            json.dump([t.to_dict() for t in self.theses], f, ensure_ascii=False, indent=2)

    def load(self):
        with open(self.file) as f:
            for d in json.load(f):
                t = Thesis(d["industry"], d.get("company", ""))
                t.__dict__.update(d)
                self.theses.append(t)

    def review(self, industry: str):
        """列出待复盘的所有论题"""
        return [t for t in self.theses if t.industry == industry]

    def summary(self):
        active = [t for t in self.theses if t.status == "active"]
        researching = [t for t in self.theses if t.status == "researching"]
        print(f"Active theses: {len(active)}")
        print(f"Researching: {len(researching)}")
        for t in active:
            print(f"  [{t.confidence}/10] {t.industry} {t.company} — {t.core_thesis[:60]}")

if __name__ == "__main__":
    import sys
    tm = ThesisManager()
    if len(sys.argv) > 1:
        if sys.argv[1] == "list":
            tm.summary()
        elif sys.argv[1] == "add" and len(sys.argv) >= 3:
            t = Thesis(sys.argv[2])
            t.core_thesis = " ".join(sys.argv[3:]) if len(sys.argv) > 3 else ""
            tm.add(t)
            print(f"Added thesis: {sys.argv[2]}")
    else:
        tm.summary()
