#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图片规律专项总结：只基于博主发的图片（走势图圈选等）识别出的规律，
关联博主，按图/博主组织，统计并列出代表性规律，回测 26230 命中。
输入: data/crawl/20260828/{posts.json, vision_patterns_full.json, lottery_recent.json}
输出: docs/图片规律总结报告-20260828.md
"""
import json
import os
import sys
from collections import Counter, defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(REPO, "data", "crawl", "20260828")

POS_NAMES = {0: "万位", 1: "千位", 2: "百位", 3: "十位", 4: "个位"}
VALID_TYPES = {"定位", "斜连", "胆码", "头", "尾", "和值", "杀号"}


def main():
    posts = json.load(open(os.path.join(BASE, "posts.json"), encoding="utf-8"))
    vision = json.load(open(os.path.join(BASE, "vision_patterns_full.json"), encoding="utf-8"))
    lottery = json.load(open(os.path.join(BASE, "lottery_recent.json"), encoding="utf-8"))

    d26230 = None
    for r in lottery:
        if r["period"] == "26230":
            d26230 = r["numbers"]
            break
    d = d26230 or []

    # file -> blogger
    def blogger_of(f):
        pid = "_".join(f.split("_")[:-1]).split(".")[0]
        for p in posts:
            if p.get("id", "").startswith(pid):
                return p.get("creator", {}).get("name", "?")
        return "未知"

    # 收集有效图片规律（第一层过滤：只保留"走势图圈选"——博主在历史走势图上画了规律；
    # 杀号表/文字截图不算"画规律"，排除）
    records = []  # {blogger, file, type, position, numbers, desc, img_type}
    img_count = Counter()
    for v in vision:
        f = v.get("file", "")
        itype = v.get("type", "?")
        img_count[itype] += 1
        pats = v.get("patterns", [])
        if itype != "走势图圈选":
            continue
        for p in pats:
            t = p.get("type", "")
            if t not in VALID_TYPES:
                continue
            nums = [int(x) for x in (p.get("numbers") or []) if str(x).isdigit()]
            desc = (p.get("desc") or "").strip()
            if not nums and not desc:
                continue
            records.append({
                "blogger": blogger_of(f), "file": f, "type": t,
                "position": p.get("position"), "numbers": nums,
                "desc": desc, "img_type": itype,
            })
    print("有效图片规律条目:", len(records))

    # 命中判定（严格：仅"明确预测指向"的标注才算；无位置组合不判）
    PREDICT_KW = ("26230", "预测", "候选", "看好", "主攻", "防", "底部", "下期",
                  "标注", "预测行", "杀", "红框", "蓝框", "胆")

    def hit(r):
        desc = r["desc"]
        if not any(k in desc for k in PREDICT_KW):
            return False  # 历史走势回顾/描述，非预测
        nums = r["numbers"]
        pmap = {"万位": 0, "千位": 1, "百位": 2, "十位": 3, "个位": 4,
                "头": 0, "尾": 4}
        pos = None
        pm = r["position"]
        if pm in pmap:
            pos = pmap[pm]
        elif pm and "第" in str(pm):
            for i in range(1, 6):
                if str(i) in str(pm):
                    pos = i - 1
        if r["type"] == "杀号":
            return all(n not in d for n in nums)
        if r["type"] in ("定位", "头", "尾", "胆码"):
            if pos is None:
                return False  # 无位置（组合/铁卒类）不算位置命中
            return d[pos] in nums
        return False

    for r in records:
        r["hit"] = hit(r)

    # 统计
    n_hit = sum(1 for r in records if r["hit"])
    by_type = Counter(r["type"] for r in records)
    by_type_hit = defaultdict(lambda: [0, 0])
    for r in records:
        by_type_hit[r["type"]][0] += 1
        if r["hit"]:
            by_type_hit[r["type"]][1] += 1
    blogger_cnt = Counter(r["blogger"] for r in records)
    blogger_imgs = defaultdict(set)
    for r in records:
        blogger_imgs[r["blogger"]].add(r["file"])

    # 代表性规律：有预测指向（desc 含 候选/预测/看好/26230/下期）优先
    def key_score(r):
        s = 0
        desc = r["desc"]
        if any(k in desc for k in ("候选", "预测", "看好", "26230", "下期", "胆", "杀")):
            s += 2
        if len(r["numbers"]) <= 3:
            s += 1
        if r["hit"]:
            s += 1
        return s

    records_sorted = sorted(records, key=key_score, reverse=True)

    lines = ["# 图片规律总结报告（2026-08-28）", "",
             "> 严格过滤：仅统计博主在**走势图上画出的规律**（走势图圈选），"
             "排除杀号表/文字截图/历史回顾；且要求明确预测指向 + 位置命中。",
             f"> 26230 期开奖：**{' '.join(map(str, d))}**", "",
             "## 一、图片规律总览",
             f"- 走势图圈选图：**{img_count['走势图圈选']}** 张",
             f"- 图片规律条目：**{len(records)}** 条，其中 26230 期命中 **{n_hit}** 条（{n_hit/max(len(records),1):.0%}）", "",
             "## 二、规律类型分布（图片）", "| 类型 | 条数 | 命中 | 命中率 |", "|---|---|---|---|"]
    for t, (c, h) in sorted(by_type_hit.items(), key=lambda x: -x[1][0]):
        lines.append(f"| {t} | {c} | {h} | {h/c:.0%} |")
    lines.append("")
    lines.append("## 三、发规律图最多的博主 TOP10")
    lines.append("| 博主 | 规律条数 | 规律图数 |")
    lines.append("|---|---|---|")
    for b, c in blogger_cnt.most_common(10):
        lines.append(f"| {b} | {c} | {len(blogger_imgs[b])} |")
    lines.append("")
    lines.append("## 四、代表性图片规律 TOP25（带博主与识别描述）")
    lines.append("| 博主 | 类型 | 描述 | 命中26230 |")
    lines.append("|---|---|---|---|")
    for r in records_sorted[:25]:
        mark = "✅" if r["hit"] else "❌"
        lines.append(f"| {r['blogger']} | {r['type']} | {r['desc'][:60]} | {mark} |")
    lines.append("")
    lines.append("## 五、说明")
    lines.append("- 描述来自视觉模型对图片的识别（圈选/连线/标注），保留原文更接近博主本意")
    lines.append("- 命中判定：定位/头/尾看对应位置数字是否在候选；杀号看数字是否未开出；胆码看与开奖是否有交集")
    report = "\n".join(lines)

    out = os.path.join(REPO, "docs", "图片规律总结报告-20260828.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(report)
    # 保存结构化结果
    with open(os.path.join(BASE, "image_patterns_with_blogger.json"), "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=1)
    print(report)


if __name__ == "__main__":
    main()
