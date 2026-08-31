#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
导出"命中规律库"：从 image_patterns_with_blogger.json 提取本期(26230)命中记录，
连同采集条数/命中率，写成 docs/规律/26230.json + 26230.md。
每期一个文件，规律累计可查。
"""
import json
import os
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(REPO, "data", "crawl", "20260828")
PATH = os.path.join(BASE, "image_patterns_with_blogger.json")
OUT_DIR = os.path.join(REPO, "docs", "规律")
os.makedirs(OUT_DIR, exist_ok=True)

PERIOD = "26230"
ACTUAL = {"万位": 9, "千位": 4, "百位": 6, "十位": 8, "个位": 3}


def real_hit(r):
    return r.get("hit") and not (r.get("type") == "杀号" and not r.get("numbers"))


def main():
    data = json.load(open(PATH, encoding="utf-8"))
    hits = [r for r in data if real_hit(r)]
    rejected = [r for r in data if r.get("reject_reason")]
    # 同博主多条 reject 记录去重（保留一条）
    seen_rej = set()
    rejected_dedup = []
    for r in rejected:
        b = r.get("blogger")
        if b not in seen_rej:
            seen_rej.add(b)
            rejected_dedup.append(r)
    rejected = rejected_dedup

    n_total = len(data)
    n_hit = len(hits)
    full = [r for r in hits if r.get("multi") == "1位置1中"]
    n_full = len(full)

    # ---- JSON ----
    rules = []
    for r in hits:
        rules.append({
            "period": PERIOD,
            "draw": "9 4 6 8 3",
            "blogger": r.get("blogger"),
            "image": r.get("file"),
            "type": r.get("type"),
            "hit_position": r.get("position"),
            "hit_numbers": r.get("numbers"),
            "multi": r.get("multi"),
            "predicted_positions": r.get("predicted_positions"),
            "pos_check": r.get("pos_check"),
            "logic": r.get("logic"),
        })
    out = {
        "period": PERIOD,
        "draw": "9 4 6 8 3",
        "total_records": n_total,
        "hit_records": n_hit,
        "hit_rate": round(n_hit / n_total, 4) if n_total else 0,
        "full_hits": n_full,
        "rejected": [{"blogger": r.get("blogger"), "reason": r.get("reject_reason")} for r in rejected],
        "rules": rules,
        "rule_count": len(rules),
    }
    with open(os.path.join(OUT_DIR, f"{PERIOD}.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    # ---- Markdown ----
    L = []
    L.append(f"# 命中规律库 — 26230 期")
    L.append("")
    L.append(f"> 期号：**26230** ｜ 开奖：**9 4 6 8 3**（万 千 百 十 个）｜ 校准行 26229 = 2 8 0 5 4")
    L.append(f"> 采集记录：**{n_total} 条** ｜ 命中：**{n_hit} 条（{n_hit/n_total:.2%}）** ｜ 完全命中（1位置1中）：{n_full} 条")
    L.append(f"> 规律条数：**{len(rules)} 条**")
    L.append("")
    L.append("| 博主 | 命中位置 | 全图预测明细（各位置对错） | 博主画规规律逻辑 |")
    L.append("|---|---|---|---|")
    for r in hits:
        blogger = r.get("blogger")
        t, pos, nums = r.get("type"), r.get("position"), r.get("numbers")
        ns = ",".join(map(str, nums)) if nums else "?"
        actual = ACTUAL.get(pos, "?")
        main = f"{t} {pos}={ns} → 26230 {pos}={actual} ✓"
        details = []
        for p in r.get("predicted_positions") or []:
            pp = p.get("位置", "?")
            cand = ",".join(map(str, p.get("候选") or []))
            a = ACTUAL.get(pp, "?")
            ok = "✓" if (a is not None and a in (p.get("候选") or [])) else "✗"
            details.append(f"{pp}{cand}{ok}(实际{a})")
        logic = r.get("logic", "")
        L.append(f"| {blogger} | {main} | {'；'.join(details)} | {logic} |")
    L.append("")
    L.append("## 被剔除的命中（本期修正）")
    L.append("")
    for r in rejected:
        L.append(f"- **{r.get('blogger')}**：{r.get('reject_reason')}")
    L.append("")
    L.append("> 规律为博主手绘画规的历史总结，彩票开奖属独立随机事件，不具备预测效力。")
    with open(os.path.join(OUT_DIR, f"{PERIOD}.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    print(f"规律库 → {OUT_DIR}/")
    print(f"  采集 {n_total} 条 / 命中 {n_hit} ({n_hit/n_total:.2%}) / 完全命中 {n_full} / 规律 {len(rules)} 条")
    print(f"  剔除 {len(rejected)} 条: {[r.get('blogger') for r in rejected]}")


if __name__ == "__main__":
    main()
