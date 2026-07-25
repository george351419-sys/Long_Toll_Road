#!/usr/bin/env python3
"""Download selected-stock and benchmark daily bars from BaoStock, resumably."""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

import baostock as bs
import pandas as pd


HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
PRICES = DATA / "prices"
FIELDS = "date,code,open,high,low,close,preclose,volume,amount,tradestatus,pctChg,isST"


def market_code(code: str) -> str:
    return ("sh." if code.startswith("6") else "sz.") + code


def fetch(code: str, path: Path, start: str, end: str) -> int:
    if path.exists():
        return len(pd.read_csv(path))
    last_error = ""
    for attempt in range(5):
        rs = bs.query_history_k_data_plus(
            code, FIELDS, start_date=start, end_date=end,
            frequency="d", adjustflag="2",
        )
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
        if rs.error_code == "0" and rows:
            frame = pd.DataFrame(rows, columns=rs.fields)
            path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_csv(path, index=False)
            return len(frame)
        last_error = rs.error_msg
        time.sleep(2 ** attempt)
    print(f"WARN {code}: {last_error}", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2016-01-01")
    parser.add_argument("--end", default="2024-12-31")
    args = parser.parse_args()
    selected = pd.read_csv(DATA / "selections.csv", dtype={"code": str})
    codes = sorted(selected["code"].str.zfill(6).unique())
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(login.error_msg)
    results = []
    try:
        for index, plain in enumerate(codes, 1):
            code = market_code(plain)
            rows = fetch(code, PRICES / f"{plain}.csv", args.start, args.end)
            results.append({"code": plain, "rows": rows})
            if index % 25 == 0:
                print(f"{index}/{len(codes)}", flush=True)
        for code, name in (("sh.000906", "CSI800"), ("sh.000300", "CSI300")):
            rows = fetch(code, PRICES / f"{name}.csv", args.start, args.end)
            results.append({"code": code, "rows": rows})
    finally:
        bs.logout()
    manifest = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "source": "BaoStock query_history_k_data_plus",
        "adjustment": "adjustflag=2 (forward adjusted)",
        "requested_codes": len(codes),
        "downloaded_codes": sum(x["rows"] > 0 for x in results[:-2]),
        "missing": [x["code"] for x in results[:-2] if x["rows"] == 0],
        "rows": results,
    }
    (DATA / "price-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in manifest.items() if k != "rows"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
