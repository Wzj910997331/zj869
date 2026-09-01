#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图片规律专项总结：只基于博主发的图片（走势图圈选等）识别出的规律，
关联博主，按图/博主组织，统计并列出代表性规律，回测指定期命中。
用法: python tools/summarize_image_patterns.py --base data/crawl/20260829 --period 26231
输入: data/crawl/{BASE}/{posts.json, vision_patterns_full.json, lottery_recent.json}
输出: docs/图片规律总结报告-{period}.md + {BASE}/image_patterns_with_blogger.json
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

POS_NAMES = {0: "万位", 1: "千位", 2: "百位", 3: "十位", 4: "个位"}
POS_CHAR = ["万", "千", "百", "十", "个"]
POS_MAP = {"万": 0, "千": 1, "百": 2, "十": 3, "个": 4, "头": 0, "尾": 4}
VALID_TYPES = {"定位", "斜连", "胆码", "头", "尾", "和值", "杀号"}
# 占位/待更新等"无实际预测"标注（desc 自否），提取时过滤
PLACEHOLDER_KW = ("待更新", "占位", "无实际", "无预测", "暂无", "未更新", "等更新")


def norm_pos(pm):
    """position 归一化：'万位'→'万'、'第2位'→'千'、区间'千位-万位'→'千-万'。
    返回 (归一化字符串, 命中用位置索引列表)。索引 0-4 对应 万千百十个。"""
    if not pm:
        return None, []
    s = str(pm).replace("位", "").strip()
    parts = [p for p in re.split(r"[-~到至]", s) if p]
    if len(parts) >= 2:
        a, b = parts[0], parts[-1]
        if a in POS_MAP and b in POS_MAP:
            return f"{a}-{b}", sorted({POS_MAP[a], POS_MAP[b]})
        return s, []
    m = re.search(r"第\s*([1-5])", s)
    if m:
        i = int(m.group(1)) - 1
        return POS_CHAR[i], [i]
    if s in POS_MAP:
        return s, [POS_MAP[s]]
    return s, []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="data/crawl/YYYYMMDD")
    ap.add_argument("--period", required=True, help="预测期号，如 26231")
    ap.add_argument("--calib", default="", help="校准期号，如 26230")
    ap.add_argument("--draw", default="", help="本期开奖，如 '1 8 7 9 9'")
    args = ap.parse_args()

    BASE = os.path.join(REPO, "data", "crawl", args.base)
    PERIOD = args.period
    posts = json.load(open(os.path.join(BASE, "posts.json"), encoding="utf-8"))
    vision = json.load(open(os.path.join(BASE, "vision_patterns_full.json"), encoding="utf-8"))
    lottery = json.load(open(os.path.join(BASE, "lottery_recent.json"), encoding="utf-8"))

    d = None
    for r in lottery:
        if r["period"] == PERIOD:
            d = r["numbers"]
            break
    if d is None:
        d = [int(x) for x in args.draw.split()]
    if not d:
        print(f"!! 找不到 {PERIOD} 开奖（lottery 与 --draw 都缺）")
        sys.exit(1)

    # file -> blogger
    def blogger_of(f):
        pid = "_".join(f.split("_")[:-1]).split(".")[0]
        for p in posts:
            if p.get("id", "").startswith(pid):
                return p.get("creator", {}).get("name", "?")
        return "未知"

    # 收集有效图片规律（第一层过滤：保留视觉模型从图片中提取出的**预测类规律标注**
    # （定位/斜连/胆码/头/尾/和值/杀号），不限图片类型——走势图圈选/文字预测截图/杀号表
    # 都含博主预测标注；仅"其他"类（无规律标注）天然为空，不会带来噪音）
    records = []  # {blogger, file, type, position, numbers, desc, img_type}
    img_count = Counter()
    for v in vision:
        f = v.get("file", "")
        itype = v.get("type", "?")
        img_count[itype] += 1
        pats = v.get("patterns", [])
        seen_in_file = set()
        for p in pats:
            t = p.get("type", "")
            if t not in VALID_TYPES:
                continue
            desc = (p.get("desc") or "").strip()
            # 过滤占位/待更新等"无实际预测"标注（desc 自否：如 '0000 待更新占位，无实际四位预测'）
            if any(k in desc for k in PLACEHOLDER_KW):
                continue
            raw = [str(x) for x in (p.get("numbers") or [])]
            nums = list(dict.fromkeys(int(x) for x in raw if x.strip().isdigit()))
            if not nums:
                continue  # 无数字的 pattern 无预测价值（且空 numbers 会让杀号命中误判为 True）
            pos, _ = norm_pos(p.get("position"))
            key = (t, pos, tuple(nums))
            if key in seen_in_file:
                continue  # 同文件完全重复的提取去重
            seen_in_file.add(key)
            records.append({
                "blogger": blogger_of(f), "file": f, "type": t,
                "position": pos, "numbers": nums,
                "desc": desc, "img_type": itype,
            })
    print("有效图片规律条目:", len(records))

    # 命中判定（严格：仅"明确预测指向"的标注才算；无位置组合不判）
    PREDICT_KW = (PERIOD, "预测", "候选", "看好", "主攻", "防", "底部", "下期",
                  "标注", "预测行", "杀", "红框", "蓝框", "胆", "推荐", "主推",
                  "关注", "大牛", "红码", "旺码", "入围", "必用", "热门", "冷门",
                  "合数", "吉数", "首选", "重点", "留意", "跟进")

    def hit(r):
        desc = r["desc"]
        # 历史期标注（提及非本期预测的期号）→ 不是对本期的预测，排除
        periods = re.findall(r"26\d{3}", desc)
        if periods and all(p != PERIOD for p in periods):
            return False
        if not any(k in desc for k in PREDICT_KW):
            return False  # 历史走势回顾/描述，非预测
        nums = r["numbers"]
        if not nums:
            return False
        if r["type"] == "杀号":
            return all(n not in d for n in nums)  # 被杀数字全部未开出
        idxs = norm_pos(r["position"])[1]  # position 已在收集时归一化，这里直接取索引
        if r["type"] in ("定位", "头", "尾"):
            if not idxs:
                return False  # 无位置（组合/铁卒类）不算位置命中
            return any(d[i] in nums for i in idxs)  # 位置范围命中任一即算
        if r["type"] == "胆码":
            return any(n in d for n in nums)  # 全盘交集：胆码数字在开奖任意位出现
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

    # 代表性规律：有预测指向（desc 含 候选/预测/看好/PERIOD/下期）优先
    def key_score(r):
        s = 0
        desc = r["desc"]
        if any(k in desc for k in ("候选", "预测", "看好", PERIOD, "下期", "胆", "杀")):
            s += 2
        if len(r["numbers"]) <= 3:
            s += 1
        if r["hit"]:
            s += 1
        return s

    records_sorted = sorted(records, key=key_score, reverse=True)

    lines = [f"# 图片规律总结报告（{BASE.split('/')[-1]}）", "",
             "> 严格过滤：仅统计博主在**图片上给出的预测规律标注**（走势图圈选/文字预测截图/杀号表"
             "均计，类型取定位/斜连/胆码/头/尾/和值/杀号）；过滤'待更新/占位'等无实际预测标注；"
             "position 归一化（万位→万、区间→千-万）后按位命中。",
             f"> {PERIOD} 期开奖：**{' '.join(map(str, d))}**", "",
             "## 一、图片规律总览",
             f"- 图片类型分布：**走势图圈选 {img_count['走势图圈选']} 张 / 文字预测截图 {img_count['文字预测截图']} 张 / 杀号表 {img_count['杀号表']} 张 / 其他 {img_count['其他']} 张**",
             f"- 图片规律条目：**{len(records)}** 条，其中 {PERIOD} 期命中 **{n_hit}** 条（{n_hit/max(len(records),1):.0%}）", "",
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
    lines.append(f"| 博主 | 类型 | 描述 | 命中{PERIOD} |")
    lines.append("|---|---|---|---|")
    for r in records_sorted[:25]:
        mark = "✅" if r["hit"] else "❌"
        lines.append(f"| {r['blogger']} | {r['type']} | {r['desc'][:60]} | {mark} |")
    lines.append("")
    lines.append("## 五、说明")
    lines.append("- 描述来自视觉模型对图片的识别（圈选/连线/标注/文字预测），保留原文更接近博主本意")
    lines.append("- 图片类型不限（走势图圈选/文字预测截图/杀号表均含博主预测标注）；类型仅作统计，不影响规律提取")
    lines.append("- position 已归一化（万位→万，区间→千-万）；占位/待更新类'无实际预测'标注已过滤；同文件重复提取已去重")
    lines.append("- 命中判定：定位/头/尾看对应位置数字是否在候选（区间任一位置命中即算）；杀号看数字是否全部未开出；胆码看与开奖全盘是否有交集")
    report = "\n".join(lines)

    out = os.path.join(REPO, "docs", f"图片规律总结报告-{BASE.split('/')[-1]}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(report)
    # 保存结构化结果
    with open(os.path.join(BASE, "image_patterns_with_blogger.json"), "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=1)
    print(report)


if __name__ == "__main__":
    main()
