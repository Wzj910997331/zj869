#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多位置复核应用脚本（2026-08-31，幂等版）。
GLM 逐张重读命中图发现：博主普遍预测 2-4 个位置，此前只记了命中的 1 个。
本脚本：
  1) 清理上次运行误加在"非命中记录"上的 predicted_positions/logic/multi/pos_check 及 desc 后缀
  2) 剔除重读判定为错误命中的记录（recheck 的 rejected 列表）→ hit=False + 原因
  3) 仅对真命中合并多位置信息，desc 补全"预测N位置M中"与规律逻辑
幂等：重复运行结果一致。
用法: python tools/apply_multipos_recheck.py --base data/crawl/20260829
"""
import argparse
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MULTI_KEYS = ("predicted_positions", "pos_check", "logic", "multi")
SUFFIX_RE = re.compile(r" ｜ 博主实际预测.*$")  # 清整段追加后缀（multi 可能含空格，[^ ]+ 会留残尾）


def base(f):
    return os.path.splitext(os.path.basename(f))[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="data/crawl/YYYYMMDD")
    ap.add_argument("--verified-by", default="glm-5.3-flash 多位置重读", help="verified_by 标记")
    args = ap.parse_args()

    BASE = os.path.join(REPO, "data", "crawl", args.base)
    PATH = os.path.join(BASE, "image_patterns_with_blogger.json")
    RECHECK = os.path.join(BASE, "glm_multipos_recheck.json")

    data = json.load(open(PATH, encoding="utf-8"))
    rk = json.load(open(RECHECK, encoding="utf-8"))

    # 过滤 error 条目：recheck 失败/缺失的图没有 predicted_positions，混入会导致下面 KeyError
    by_file = {base(h["file"]): h for h in rk["hits"] if not h.get("error")}
    rej = {base(r["file"]): r for r in rk["rejected"] if not r.get("error")}

    # 1) 清理：所有记录去掉 multi 字段 + desc 后缀
    cleaned = 0
    for r in data:
        for k in MULTI_KEYS:
            if r.pop(k, None) is not None:
                cleaned += 1
        d = r.get("desc") or ""
        if SUFFIX_RE.search(d):
            r["desc"] = SUFFIX_RE.sub("", d)
            cleaned += 1

    # 2) 剔除两条错误命中
    for r in data:
        if base(r.get("file")) in rej:
            rj = rej[base(r.get("file"))]
            r["hit"] = False
            r["verified_by"] = f"{args.verified_by}（剔除）"
            r["reject_reason"] = rj["reason"]
            r["multi"] = rj["multi"]

    # 3) 仅对命中记录合并多位置信息
    applied = []
    for r in data:
        b = base(r.get("file"))
        if b in by_file and r.get("hit"):
            h = by_file[b]
            r["predicted_positions"] = h["predicted_positions"]
            r["pos_check"] = h["pos_check"]
            r["logic"] = h["logic"]
            r["multi"] = h["multi"]
            r["verified_by"] = args.verified_by
            if SUFFIX_RE.search(r.get("desc") or ""):
                r["desc"] = SUFFIX_RE.sub("", r.get("desc") or "")
            r["desc"] = f"{r['desc']} ｜ 博主实际预测{h['multi']}"
            applied.append(base(r["file"]))

    json.dump(data, open(PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    hits = [r for r in data if r.get("hit") and not (r.get("type") == "杀号" and not r.get("numbers"))]
    leftover = [r for r in data if r.get("predicted_positions") and not r.get("hit")]
    print(f"清理字段 {cleaned} 处")
    print(f"合并多位置信息到命中记录 {len(applied)} 条")
    print(f"剔除错误命中 {len(rej)} 条")
    print(f"最终命中 {len(hits)} 条 / 总条数 {len(data)} ({len(hits)/len(data):.2%})")
    print(f"残留(非命中带predicted_positions): {len(leftover)} ← 应为0")
    print()
    for r in hits:
        np_ = len(r.get("predicted_positions", []))
        print(f"  {r['blogger']:<10} {r['multi']:<16} 预测位置数={np_}")

    from collections import Counter
    print()
    print("多位置分布:", dict(Counter(r.get("multi") for r in hits)))


if __name__ == "__main__":
    main()
