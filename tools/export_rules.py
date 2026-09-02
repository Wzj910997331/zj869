#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
导出"命中规律库"：从 image_patterns_with_blogger.json 提取本期命中记录，
连同采集条数/命中率，写成 docs/规律/{period}.json + {period}.md。
每期一个文件，规律累计可查。
用法: python tools/export_rules.py --base data/crawl/20260829 --period 26231 --draw "1 8 7 9 9" --calib 26230 --calib-draw "9 4 6 8 3"
"""
import argparse
import json
import os
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def norm_pos(s):
    """position 归一化：'万位'→'万'，兼容 summarize 单字输出与 GLM 重读的带位后缀。"""
    if not s:
        return s
    return str(s).replace("位", "").strip()


def real_hit(r):
    """严格命中口径：只有博主在走势图上画出的、有明确位置的预测才算命中。

    杀号（杀掉不开出的号码）、报号/铁率（文字预测截图/杀号表等，博主直接打字报数、
    无画规）、不定位组合（无位置）都不体现画规规律，一律不算命中。"""
    if not r.get("hit"):
        return False
    if r.get("type") == "杀号":
        return False
    if r.get("img_type") != "走势图圈选":
        return False
    if not r.get("position"):
        return False
    return True


def reject_reason(r):
    """被剔除的命中候选的剔除原因（优先用已记录的原因，否则按口径推导）。"""
    if r.get("reject_reason"):
        return r.get("reject_reason")
    if r.get("img_type") != "走势图圈选":
        return f"报号/铁率（{r.get('img_type')}）：博主文字报数/缩水推荐，不体现画规"
    if not r.get("position"):
        return "不定位（无位置）：胆码全盘/组合推荐，非定位画规"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="data/crawl/YYYYMMDD")
    ap.add_argument("--period", required=True, help="预测期号，如 26231")
    ap.add_argument("--draw", required=True, help="本期开奖，如 '1 8 7 9 9'")
    ap.add_argument("--calib", default="", help="校准期号，如 26230")
    ap.add_argument("--calib-draw", default="", help="校准期开奖，如 '9 4 6 8 3'")
    args = ap.parse_args()

    BASE = os.path.join(REPO, "data", "crawl", args.base)
    PATH = os.path.join(BASE, "image_patterns_with_blogger.json")
    OUT_DIR = os.path.join(REPO, "docs", "规律")
    os.makedirs(OUT_DIR, exist_ok=True)

    PERIOD = args.period
    DRAW = args.draw
    ACTUAL = dict(zip(["万", "千", "百", "十", "个"], [int(x) for x in DRAW.split()]))
    CALIB = f"{args.calib} = {args.calib_draw}" if args.calib_draw else ""
    data = json.load(open(PATH, encoding="utf-8"))
    # 采集口径：仅博主画规（走势图圈选、非杀号）；杀号与报号/铁率不体现画规，整体剔除
    kept = [r for r in data if r.get("type") != "杀号" and r.get("img_type") == "走势图圈选"]
    hits = [r for r in kept if real_hit(r)]
    # 被剔除的命中候选：识别为命中但不符严格口径（报号/铁率、无位置）→ 展示在"被剔除"节
    rejected = [r for r in data if r.get("hit") and not real_hit(r) and r.get("type") != "杀号"]
    # 同博主多条 reject 记录去重（保留一条）
    seen_rej = set()
    rejected_dedup = []
    for r in rejected:
        b = r.get("blogger")
        if b not in seen_rej:
            seen_rej.add(b)
            rejected_dedup.append(r)
    rejected = rejected_dedup

    n_total = len(kept)
    n_hit = len(hits)
    hit_rate = n_hit / n_total if n_total else 0
    full = [r for r in hits if r.get("multi") == "1位置1中"]
    n_full = len(full)

    # ---- JSON ----
    rules = []
    for r in hits:
        rules.append({
            "period": PERIOD,
            "draw": DRAW,
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
        "draw": DRAW,
        "total_records": n_total,
        "hit_records": n_hit,
        "hit_rate": round(hit_rate, 4),
        "full_hits": n_full,
        "rejected": [{"blogger": r.get("blogger"), "reason": reject_reason(r)} for r in rejected],
        "rules": rules,
        "rule_count": len(rules),
    }
    with open(os.path.join(OUT_DIR, f"{PERIOD}.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    # ---- Markdown ----
    L = []
    L.append(f"# 命中规律库 — {PERIOD} 期")
    L.append("")
    L.append(f"> 期号：**{PERIOD}** ｜ 开奖：**{DRAW}**（万 千 百 十 个）｜ 校准行 {CALIB}")
    L.append(f"> 采集记录：**{n_total} 条** ｜ 命中：**{n_hit} 条（{hit_rate:.2%}）** ｜ 完全命中（1位置1中）：{n_full} 条")
    L.append(f"> 规律条数：**{len(rules)} 条**")
    L.append("")
    L.append("| 博主 | 命中位置 | 全图预测明细（各位置对错） | 博主画规规律逻辑 |")
    L.append("|---|---|---|---|")
    for r in hits:
        blogger = r.get("blogger")
        t, pos, nums = r.get("type"), norm_pos(r.get("position")), r.get("numbers")
        ns = ",".join(map(str, nums)) if nums else "?"
        if pos in ACTUAL:  # 定位/头/尾等单位置：可对位展示
            actual = ACTUAL[pos]
            main = f"{t} {pos}={ns} → {PERIOD} {pos}={actual} ✓"
        else:  # 胆码（全盘）/区间 position：无法单位置对位，标全盘命中
            main = f"{t} {ns} → 全盘命中 ✓"
        details = []
        for p in r.get("predicted_positions") or []:
            pp = norm_pos(p.get("位置", "?"))
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
        L.append(f"- **{r.get('blogger')}**：{reject_reason(r)}")
    L.append("")
    L.append("> 规律为博主手绘画规的历史总结，彩票开奖属独立随机事件，不具备预测效力。")
    with open(os.path.join(OUT_DIR, f"{PERIOD}.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    print(f"规律库 → {OUT_DIR}/")
    print(f"  采集 {n_total} 条 / 命中 {n_hit} ({hit_rate:.2%}) / 完全命中 {n_full} / 规律 {len(rules)} 条")
    print(f"  剔除 {len(rejected)} 条: {[r.get('blogger') for r in rejected]}")


if __name__ == "__main__":
    main()
