#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
导出"二次检查"图片包：把有预测指向的博主规律图 + 每张图的 GLM 预测读数
整理到 data/crawl/20260828/二次检查/（图/ + README.md 对照表），供人工核验预测是否准确。
输入: image_patterns_with_blogger.json（GLM 修正后 700 条）+ images/ 原图
输出: data/crawl/20260828/二次检查/{README.md, 图/NNN_博主_原文件.jpg}
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
OUT = os.path.join(BASE, "二次检查")
IMG_OUT = os.path.join(OUT, "图")
os.makedirs(IMG_OUT, exist_ok=True)

POS_NAMES = {0: "万位", 1: "千位", 2: "百位", 3: "十位", 4: "个位"}
# 有预测指向的关键词（与 summarize_image_patterns.py 的 PREDICT_KW 对齐）
PREDICT_KW = ("26230", "预测", "候选", "看好", "主攻", "防", "底部", "下期",
              "标注", "预测行", "杀", "红框", "蓝框", "胆")


def find_img(f):
    """images/ 下找原图，处理 jpg/png 扩展名差异"""
    p = os.path.join(IMGDIR, f)
    if os.path.exists(p):
        return p
    base, _ = os.path.splitext(f)
    for ext in (".jpg", ".png", ".jpeg"):
        q = os.path.join(IMGDIR, base + ext)
        if os.path.exists(q):
            return q
    return None


def fmt_pred(r):
    t, pos, nums = r.get("type"), r.get("position"), r.get("numbers")
    ns = ",".join(map(str, nums)) if nums else "(空)"
    if t == "定位" and pos:
        return f"定位 {pos}={ns}"
    if t == "杀号":
        if not nums:
            return f"杀号 未识别数字⚠️"
        return f"杀号 杀{ns}"
    if t in ("头", "尾") and pos:
        return f"{t} {pos}={ns}"
    if t == "胆码":
        return f"胆码 {ns}"
    return f"{t} {pos or ''}={ns}"


def main():
    data = json.load(open(os.path.join(BASE, "image_patterns_with_blogger.json"), encoding="utf-8"))

    # 分组：有预测指向的记录按图聚合
    groups = defaultdict(list)
    for r in data:
        desc = r.get("desc") or ""
        if any(k in desc for k in PREDICT_KW):
            groups[r["file"]].append(r)

    by_blogger = defaultdict(list)
    for f in groups:
        by_blogger[groups[f][0]["blogger"]].append(f)

    idx = 0
    rows = []
    missing = []
    for blogger in sorted(by_blogger):
        for f in sorted(by_blogger[blogger]):
            idx += 1
            src = find_img(f)
            name = os.path.splitext(os.path.basename(f))[0]
            if src:
                _, ext = os.path.splitext(os.path.basename(src))
            else:
                ext = ".jpg"
            dst = os.path.join(IMG_OUT, f"{idx:03d}_{blogger}_{name}{ext}")
            if src:
                shutil.copy(src, dst)
            else:
                missing.append(f)
            rows.append((idx, blogger, name, dst, groups[f]))

    # 统计（空号杀号 = 未识别数字的假命中，不计入命中）
    def real_hit(r):
        return r.get("hit") and not (r.get("type") == "杀号" and not r.get("numbers"))

    n_hit = sum(1 for _, _, _, _, rs in rows for r in rs if real_hit(r))
    n_emptykill = sum(1 for _, _, _, _, rs in rows for r in rs
                      if r.get("type") == "杀号" and not r.get("numbers"))
    n_rec = sum(len(rs) for _, _, _, _, rs in rows)
    n_img = len(rows)
    print(f"导出 {n_img} 张图 / {n_rec} 条预测记录 / 真实命中 {n_hit} / 空号杀号(假命中) {n_emptykill}")
    if missing:
        print("缺图:", missing)

    # README
    L = []
    L.append("# 二次检查（人工核验 GLM 预测准确性）")
    L.append("")
    L.append("> 26230 期实际开奖：**9 4 6 8 3**（万 千 百 十 个）")
    L.append("> 校准行 26229 = **2 8 0 5 4**：每张图读预测前先用此行验证列位置。")
    L.append(f"> 共 {n_img} 张有预测指向的图 / {n_rec} 条预测记录 / 真实命中 {n_hit} 条。")
    L.append("> **请重点核对这三处 GLM 修正的博主**：微时光（列偏移假命中已改）、乐仔👑1288（红格=预测非杀号，位置已改）、辉拓数据（X 打空格已剔除）。")
    L.append("")
    L.append("## 命中图（GLM 判定，请人工确认）")
    L.append("")
    for i, blogger, base, dst, rs in rows:
        hits = [r for r in rs if real_hit(r)]
        if not hits:
            continue
        for r in hits:
            L.append(f"- **{i:03d} {blogger}** ｜ {os.path.basename(dst)} ｜ `{fmt_pred(r)}` ✅")
    if n_emptykill:
        L.append("")
        L.append(f"## ⚠️ 空号杀号（{n_emptykill} 条：杀号未识别出数字，hit=True 是空列表 all() 假象，不能算命中，待人工核图确认是否剔除）")
        L.append("")
        for i, blogger, base, dst, rs in rows:
            for r in rs:
                if r.get("type") == "杀号" and not r.get("numbers"):
                    L.append(f"- **{i:03d} {blogger}** ｜ {os.path.basename(dst)} ｜ `{fmt_pred(r)}` ｜ {r.get('desc','')[:40]}")
    L.append("")
    L.append("## 全量对照表")
    L.append("")
    L.append("| 图 | 博主 | 文件 | GLM 预测记录 | 命中 |")
    L.append("|---|---|---|---|---|")
    for i, blogger, base, dst, rs in rows:
        preds = "; ".join(f"{fmt_pred(r)}{'✅' if real_hit(r) else ''}" for r in rs)
        L.append(f"| {i:03d} | {blogger} | {os.path.basename(dst)} | {preds} | {'✅' if any(real_hit(r) for r in rs) else '—'} |")
    L.append("")
    L.append("## 说明")
    L.append("- 预测读数来自 GLM-5.3-flash 对走势图的读图（校准行法定位），非 deepseek 旧读数。")
    L.append("- 命中语义：定位=该位置实际数字 ∈ 候选；杀号=被杀数字未开出。")
    L.append("- 若你看图后认为 GLM 读错（位置/数字/语义），告诉我图号即可，我来修正记录。")

    with open(os.path.join(OUT, "README.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("README →", os.path.join(OUT, "README.md"))
    print("图片 →", IMG_OUT)


if __name__ == "__main__":
    main()
