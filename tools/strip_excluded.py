#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
strip_excluded.py — 用裁剪排除清单过滤 vision_patterns_full.json。

裁剪管线（crop_all.py）会把无博主标注（no-anno / no-grid）的图剔除，
排除清单落在 data/recognize/<date>_all/exclude_list.json（decision=excluded）。
本脚本删除 vision_patterns_full.json 中这些图的记录，保证全流程分析
（summarize → recheck → apply → export）口径与排除清单一致。

用法:
  python tools/strip_excluded.py --base 20260829 --exclude data/recognize/20260829_all/exclude_list.json
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
    ap.add_argument("--exclude", required=True, help="exclude_list.json 路径")
    ap.add_argument("--write", action="store_true", help="写回文件（默认只统计不落盘）")
    args = ap.parse_args()

    BASE = os.path.join(REPO, "data", "crawl", args.base)
    path = os.path.join(BASE, "vision_patterns_full.json")
    if not os.path.exists(path):
        print(f"找不到 {path}")
        sys.exit(2)
    data = json.load(open(path, encoding="utf-8"))
    excl = json.load(open(args.exclude, encoding="utf-8"))
    # decision=keep 的图是验证后被捞回的，不能删；其余（decision 缺失/None/excluded）都删
    excluded = {k for k, v in excl.get("excluded", {}).items() if v.get("decision") != "keep"}
    if not excluded:
        print(f"排除清单为空: {args.exclude}")
        return

    kept, dropped = [], []
    for v in data:
        if v.get("file") in excluded:
            dropped.append(v)
        else:
            kept.append(v)
    print(f"原 {len(data)} 条 → 剔除 {len(dropped)} 条（excluded 图）→ 保留 {len(kept)} 条")
    if dropped:
        from collections import Counter
        print("剔除图类型分布:", dict(Counter(v.get("type", "?") for v in dropped)))
        for v in dropped[:5]:
            print(f"  - {v['file']}  {v.get('type', '?')}  err={v.get('error', '')}")
        if len(dropped) > 5:
            print(f"  ... 其余 {len(dropped) - 5} 条")
    if args.write:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(kept, f, ensure_ascii=False, indent=1)
        with open(path + ".progress", "w", encoding="utf-8") as f:
            f.write(str(len(kept)))
        print(f"已写回 {path}（{len(kept)} 条）")
    else:
        print("（未写回，加 --write 生效）")


if __name__ == "__main__":
    main()
