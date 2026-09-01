#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch_lottery.py — 抓排列5历史开奖，写 lottery_recent.json（最新在前）。

数据源：500 彩票网 history.php（gb2312）。输出 schema 与现有
data/crawl/*/lottery_recent.json 一致：[{"period":"26233","numbers":[1,6,3,4,0],"date":"2026-08-31"}, ...]

用法：
  /usr/bin/python3 tools/fetch_lottery.py --out data/crawl/20260831/lottery_recent.json [--limit 60]
"""
import argparse
import json
import os
import re
import sys
import urllib.request

HISTORY_URL = "https://datachart.500.com/plw/history/inc/history.php?limit={limit}"


def fetch_raw(limit, timeout=20):
    req = urllib.request.Request(
        HISTORY_URL.format(limit=limit), headers={
            "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                           "Chrome/120 Safari/537.36"),
            "Referer": "https://datachart.500.com/plw/history/history.shtml"})
    raw = urllib.request.urlopen(req, timeout=timeout).read()
    try:
        return raw.decode("gb2312")
    except UnicodeDecodeError:
        return raw.decode("gb18030", "replace")


def parse_rows(txt):
    """t_tr1 行 → [{period, numbers, date}]（网页顺序=最新在前）。"""
    out = []
    for r in re.findall(r'<tr class="t_tr1">(.*?)</tr>', txt, re.S):
        tds = [re.sub(r"<[^>]+>", "", t).strip()
               for t in re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)]
        if len(tds) < 3:
            continue
        _, period, nums, _a, _b, date = tds[:6]
        digits = [int(x) for x in re.findall(r"\d", nums)]
        if len(digits) == 5 and re.fullmatch(r"\d{5}", period):
            out.append({"period": period, "numbers": digits, "date": date})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=60)
    args = ap.parse_args()

    rows = parse_rows(fetch_raw(args.limit))
    if not rows:
        print("[fetch_lottery] ERROR: 未解析到开奖行")
        sys.exit(2)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print(f"[fetch_lottery] {len(rows)} 期，最新 {rows[0]['period']}={rows[0]['numbers']}"
          f"（{rows[0]['date']}）-> {args.out}")


if __name__ == "__main__":
    main()
