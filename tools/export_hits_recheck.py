#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
导出"命中核对"包：把 GLM 判定真实命中（预测正确）的博主规律图 + 命中说明
整理到 data/crawl/20260828/二次检查/命中核对/（图/ + 命中清单.md）。
输入: image_patterns_with_blogger.json（GLM 修正后）+ images/ 原图
"""
import json
import os
import shutil
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(REPO, "data", "crawl", "20260828")
IMGDIR = os.path.join(BASE, "images")
OUT = os.path.join(BASE, "二次检查", "命中核对")
IMG_OUT = os.path.join(OUT, "图")
os.makedirs(IMG_OUT, exist_ok=True)

ACTUAL = [9, 4, 6, 8, 3]  # 26230 开奖（万 千 百 十 个）
POS = {0: "万位", 1: "千位", 2: "百位", 3: "十位", 4: "个位"}
POSIDX = {"万位": 0, "千位": 1, "百位": 2, "十位": 3, "个位": 4}


def real_hit(r):
    return r.get("hit") and not (r.get("type") == "杀号" and not r.get("numbers"))


def find_img(f):
    p = os.path.join(IMGDIR, f)
    if os.path.exists(p):
        return p
    base, _ = os.path.splitext(f)
    for ext in (".jpg", ".png", ".jpeg"):
        q = os.path.join(IMGDIR, base + ext)
        if os.path.exists(q):
            return q
    return None


def hit_explain(r):
    """命中说明：预测 X 命中实际哪个位置"""
    t, pos, nums = r.get("type"), r.get("position"), r.get("numbers")
    ns = ",".join(map(str, nums)) if nums else "?"
    if t == "定位" and pos in POSIDX:
        actual = ACTUAL[POSIDX[pos]]
        return f"定位 {pos}={ns} → 26230 {pos}={actual} ✓"
    if t in ("头", "尾") and pos in POSIDX:
        actual = ACTUAL[POSIDX[pos]]
        return f"{t} {pos}={ns} → 26230 {pos}={actual} ✓"
    if t == "胆码":
        hit_n = [n for n in (nums or []) if n in ACTUAL]
        return f"胆码 {ns} → 26230 含 {hit_n} ✓"
    if t == "杀号":
        return f"杀号 {ns} → 均未开出 ✓"
    return f"{t} {pos or ''}={ns} → 命中 ✓"


def full_explain(r):
    """多位置完整说明：博主实际预测了哪些位置，各位置对错"""
    pp = r.get("predicted_positions") or []
    if not pp:
        return hit_explain(r)
    parts = []
    for p in pp:
        pos = p.get("位置", "?")
        cand = ",".join(map(str, p.get("候选") or []))
        mark = p.get("标注方式", "")
        actual = ACTUAL[POSIDX[pos]] if pos in POSIDX else None
        ok = "✓" if (actual is not None and actual in (p.get("候选") or [])) else "✗"
        parts.append(f"{pos}{cand}{ok}(实际{actual})")
    main = hit_explain(r)
    return f"{main} ｜ 全图预测: {'；'.join(parts)}"


def main():
    data = json.load(open(os.path.join(BASE, "image_patterns_with_blogger.json"), encoding="utf-8"))
    hits = [r for r in data if real_hit(r)]
    by_file = defaultdict(list)
    for r in hits:
        by_file[r["file"]].append(r)

    idx = 0
    rows = []
    missing = []
    for f in sorted(by_file):
        blogger = by_file[f][0]["blogger"]
        src = find_img(f)
        name = os.path.splitext(os.path.basename(f))[0]
        if src:
            _, ext = os.path.splitext(os.path.basename(src))
        else:
            ext = ".jpg"
        idx += 1
        dst = os.path.join(IMG_OUT, f"{idx:03d}_{blogger}_{name}{ext}")
        if src:
            shutil.copy(src, dst)
        else:
            missing.append(f)
        rows.append((idx, blogger, os.path.basename(dst), by_file[f]))

    print(f"命中图 {len(rows)} 张 / 命中记录 {len(hits)} 条")
    if missing:
        print("缺图:", missing)

    L = []
    L.append("# 命中核对（GLM 判定预测正确，请逐张确认）")
    L.append("")
    L.append("> 26230 期实际开奖：**9 4 6 8 3**（万 千 百 十 个）｜ 校准行 26229 = 2 8 0 5 4")
    L.append(f"> 共 {len(rows)} 张命中图 / {len(hits)} 条命中记录。")
    L.append("> ⚠️ **重要更正**：博主一张图往往预测 2-4 个位置，此前的『命中』只取了其中命中位置的 1 个。现在已逐张重读，把**博主实际预测的全部位置**列出并逐个判定。")
    L.append("")
    L.append("| 图 | 博主 | 文件 | 命中位置 | 全图预测明细（各位置对错） | 博主画规逻辑 |")
    L.append("|---|---|---|---|---|---|")
    for idx, blogger, fname, rs in rows:
        expls = "; ".join(hit_explain(r) for r in rs)
        details = "; ".join(full_explain(r) for r in rs)
        logic = rs[0].get("logic", "")
        L.append(f"| {idx:03d} | {blogger} | {fname} | {expls} | {details} | {logic} |")
    L.append("")
    L.append("## 说明")
    L.append("- 只包含 GLM 判定命中的记录（空号杀号假命中已排除）。")
    L.append("- 若你核图后认为某张 GLM 读错（位置/数字/语义），报图号即可，我来改。")
    with open(os.path.join(OUT, "命中清单.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("清单 →", os.path.join(OUT, "命中清单.md"))
    print("图   →", IMG_OUT)


if __name__ == "__main__":
    main()
