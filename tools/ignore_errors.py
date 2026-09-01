#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ignore_errors.py — 忽略（剔除）识别 error 条目。

用户决定不再救 error 时，把 vision_patterns_full.json 中带 error 字段的记录
移除，存档到 {base}_ignored_errors.json（保留信息以便日后补救），
主文件只剩有效记录，保证全流程（summarize → recheck → apply → export）
口径为"有效采集条数"，报告不含 error。

用法:
  python tools/ignore_errors.py --base 20260830 --write
"""
import argparse
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="YYYYMMDD（脚本自动拼 data/crawl/ 前缀）")
    ap.add_argument("--write", action="store_true", help="写回（默认只统计）")
    args = ap.parse_args()

    BASE = os.path.join(REPO, "data", "crawl", args.base)
    path = os.path.join(BASE, "vision_patterns_full.json")
    if not os.path.exists(path):
        print(f"找不到 {path}")
        sys.exit(2)
    data = json.load(open(path, encoding="utf-8"))
    errs = [v for v in data if v.get("error")]
    kept = [v for v in data if not v.get("error")]
    print(f"{args.base}: 原 {len(data)} 条 → 忽略 error {len(errs)} → 保留 {len(kept)} 条")
    if args.write:
        if errs:
            json.dump(errs, open(os.path.join(BASE, "ignored_errors.json"), "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
        json.dump(kept, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        with open(path + ".progress", "w", encoding="utf-8") as f:
            f.write(str(len(kept)))
        print(f"  error 已存档 {BASE}/ignored_errors.json，主文件保留 {len(kept)} 条")
    else:
        print("  （未写回，加 --write 生效）")


if __name__ == "__main__":
    main()
