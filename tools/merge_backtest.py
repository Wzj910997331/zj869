#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
M2：规律去重合并 + 26230 期真实开奖回测。
输入: data/crawl/20260828/{posts.json, vision_patterns_full.json, lottery_recent.json}
输出: data/crawl/20260828/pattern_pool.json + 回测报告
"""
import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "crawl", "20260828")

# ---------- 位置映射 ----------
POS_MAP = {"万位": 0, "千位": 1, "百位": 2, "十位": 3, "个位": 4,
           "第1位": 0, "第2位": 1, "第3位": 2, "第4位": 3, "第5位": 4,
           "第一位": 0, "第二位": 1, "第三位": 2, "第四位": 3, "第五位": 4,
           "头": 0, "尾": 4}
POS_NAMES = {0: "万位", 1: "千位", 2: "百位", 3: "十位", 4: "个位"}


def norm_position(p):
    if not p:
        return None
    p = str(p)
    m = re.search(r"(第\s*([1-5])\s*位|[万千百十个]位|[第]?[一二三四五]位|[头尾])", p)
    if not m:
        return None
    key = m.group(1).replace(" ", "")
    if key in ("头",):
        return 0
    if key in ("尾",):
        return 4
    if key in POS_MAP:
        return POS_MAP[key]
    c = re.search(r"([1-5])", key)
    if c:
        return int(c.group(1)) - 1
    return None


# ---------- 文字规律提取 ----------
def extract_text_patterns(posts):
    pats = []
    for p in posts:
        content = p.get("content") or ""
        blogger = p.get("creator", {}).get("name", "?")
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            t = None
            pos = None
            nums = []
            # 定位：万位/千位/百位/十位/个位 + 数字
            m = re.search(r"(万位|千位|百位|十位|个位)\s*[:：]?\s*([0-9,，\s]+)", line)
            if m:
                t = "定位"
                pos = norm_position(m.group(1))
                nums = sorted(set(int(x) for x in re.findall(r"\d", m.group(2))))
            elif "杀" in line:
                m = re.search(r"杀\s*([0-9,，\s]+)", line)
                if m:
                    t = "杀号"
                    nums = sorted(set(int(x) for x in re.findall(r"\d", m.group(1))))
                else:
                    continue
            elif re.search(r"([0-9,，\s]+)\s*头", line):
                m = re.search(r"([0-9,，\s]+)\s*头", line)
                t = "头"
                pos = 0
                nums = sorted(set(int(x) for x in re.findall(r"\d", m.group(1))))
            elif re.search(r"([0-9,，\s]+)\s*尾", line):
                m = re.search(r"([0-9,，\s]+)\s*尾", line)
                t = "尾"
                pos = 4
                nums = sorted(set(int(x) for x in re.findall(r"\d", m.group(1))))
            elif re.fullmatch(r"[0-9,\s\-—－]+", line) and len(re.findall(r"\d", line)) >= 3:
                t = "数字串"
                nums = sorted(set(int(x) for x in re.findall(r"\d", line)))
            if t and nums:
                pats.append({"type": t, "position": pos, "numbers": nums,
                             "source": "text", "blogger": blogger})
    return pats


# ---------- 图片规律标准化 ----------
def norm_image_patterns(vision):
    pats = []
    for img in vision:
        f = img.get("file", "")
        for p in img.get("patterns", []):
            t = p.get("type", "其他")
            if t == "其他":
                continue
            pos = norm_position(p.get("position"))
            nums = [int(x) for x in (p.get("numbers") or []) if str(x).isdigit()]
            nums = sorted(set(nums))
            if t == "斜连" or not nums:
                continue
            # 胆码归为定位（位置未知则空）
            if t == "胆码":
                t = "定位"
            pats.append({"type": t, "position": pos, "numbers": nums,
                         "source": "image", "file": f})
    return pats


# ---------- 回测判定 ----------
def check_hit(pat, d):
    """d = 开奖 5 位数字列表"""
    t = pat["type"]
    nums = pat["numbers"]
    pos = pat["position"]
    if t == "杀号":
        return all(n not in d for n in nums), "杀的数字全部未开出"
    if t in ("定位", "头", "尾"):
        if pos is None:
            return False, "位置未知"
        hit = any(x in nums for x in [d[pos]])
        return hit, f"{POS_NAMES.get(pos,pos)}={d[pos]} {'∈' if hit else '∉'} 候选"
    if t == "数字串":
        hit = set(nums) & set(d)
        return bool(hit), f"交集={sorted(hit)}"
    if t == "和值":
        s = sum(d)
        cand = set(nums)
        for a in nums:
            for b in nums:
                cand.add(a * 10 + b)
        return s in cand, f"和值={s} ∈ 候选{cand}"
    return False, "暂不支持回测"


def main():
    posts = json.load(open(os.path.join(BASE, "posts.json"), encoding="utf-8"))
    vision = json.load(open(os.path.join(BASE, "vision_patterns_full.json"), encoding="utf-8"))
    lottery = json.load(open(os.path.join(BASE, "lottery_recent.json"), encoding="utf-8"))

    text_pats = extract_text_patterns(posts)
    img_pats = norm_image_patterns(vision)
    print("text patterns:", len(text_pats), "| image patterns(标准化):", len(img_pats))

    # 合并规律池（按 type+position+numbers 归一）
    pool = {}  # key -> {type, position, numbers, support, sources[]}
    def add(pat):
        key = (pat["type"], pat["position"], tuple(pat["numbers"]))
        if key not in pool:
            pool[key] = {"type": pat["type"], "position": pat["position"],
                         "numbers": pat["numbers"], "support": 0,
                         "source_text": 0, "source_image": 0}
        pool[key]["support"] += 1
        if pat["source"] == "text":
            pool[key]["source_text"] += 1
        else:
            pool[key]["source_image"] += 1

    for p in text_pats:
        add(p)
    for p in img_pats:
        add(p)

    items = list(pool.values())
    print("合并后规律池(去重):", len(items))

    # 回测 26230
    d26230 = None
    for r in lottery:
        if r["period"] == "26230":
            d26230 = r["numbers"]
            break
    print("26230 开奖:", d26230)

    results = []
    for it in items:
        hit, reason = check_hit(it, d26230)
        it["hit_26230"] = hit
        it["reason"] = reason
        results.append(it)

    total = len(results)
    hit_n = sum(1 for r in results if r["hit_26230"])
    print(f"回测: 可判定 {total} 条, 命中 {hit_n} ({hit_n/total:.1%})")

    by_type = defaultdict(lambda: [0, 0])
    for r in results:
        by_type[r["type"]][0] += 1
        if r["hit_26230"]:
            by_type[r["type"]][1] += 1

    # 输出
    out_pool = os.path.join(BASE, "pattern_pool.json")
    with open(out_pool, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)

    lines = ["# M2 规律合并与回测报告（2026-08-28）", "",
             f"> 26230 期真实开奖：**{' '.join(map(str, d26230))}**", "",
             "## 规律池", f"- 合并去重后规律：**{total}** 条（文字 {len(text_pats)} 条来源 + 图片 {len(img_pats)} 条来源，按 类型+位置+数字 合并）",
             f"- 26230 期回测命中：**{hit_n}** 条（{hit_n/total:.1%}）", "",
             "## 按类型命中率", "| 类型 | 条数 | 命中 | 命中率 |", "|---|---|---|---|"]
    for t, (c, h) in sorted(by_type.items(), key=lambda x: -x[1][0]):
        lines.append(f"| {t} | {c} | {h} | {h/c:.0%} |")
    lines.append("")
    lines.append("## TOP 命中规律（支持度≥2 且命中）")
    lines.append("| 类型 | 位置 | 数字 | 支持度 | 判定 |")
    lines.append("|---|---|---|---|---|")
    for r in sorted([x for x in results if x["hit_26230"] and x["support"] >= 2],
                    key=lambda x: -x["support"])[:15]:
        pos = POS_NAMES.get(r["position"], r["position"] or "-")
        lines.append(f"| {r['type']} | {pos} | {','.join(map(str, r['numbers']))} | {r['support']} | {r['reason']} |")
    lines.append("")
    lines.append("## 支持度最高的规律 TOP10（含未命中）")
    lines.append("| 类型 | 位置 | 数字 | 支持度 | 命中26230 |")
    lines.append("|---|---|---|---|---|")
    for r in sorted(results, key=lambda x: -x["support"])[:10]:
        pos = POS_NAMES.get(r["position"], r["position"] or "-")
        lines.append(f"| {r['type']} | {pos} | {','.join(map(str, r['numbers']))} | {r['support']} | {'✅' if r['hit_26230'] else '❌'} |")
    report = "\n".join(lines)

    out_report = os.path.join(BASE, "M2回测报告.md")
    with open(out_report, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)


if __name__ == "__main__":
    main()
