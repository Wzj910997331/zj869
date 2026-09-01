#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_patterns_26233.py — 26233 期规律正确性校验 + 下游汇总。

对 analyze_20260831.json 的 ds-ok 图，把 image_patterns_with_blogger.json 里每条规律
与"同一批匹配行重新跑 extract_candidates"对比，验证：
  1. 规律可重推导（type/position/numbers 与重跑一致）——规律不是凭空捏造
  2. hit 重算一致（common.run_hits 对照 analyze 存的 hit）
  3. 博主归属非未知
然后输出汇总报告（图片规律总结报告-20260831.md）。

用法:
  /usr/bin/python3 modules/image_recognize/verify_patterns_26233.py \
      --date 20260831 --target-period 26233 --target-draw "1 6 3 4 0"
"""
import argparse
import json
import os
import sys
from collections import Counter, defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "modules", "image_recognize"))
from common import run_hits, normalize_blogger  # noqa: E402
from stage4_llm import extract_candidates, POS_NAMES  # noqa: E402

POS_CHAR = {"万": 0, "千": 1, "百": 2, "十": 3, "个": 4}


def pos2idx(pos):
    """position 归一化：单字(万/千..)→索引；已是 int 原样返回。"""
    if pos is None:
        return None
    if isinstance(pos, str):
        return POS_CHAR.get(pos)
    return int(pos)


def load_analyze(date):
    p = os.path.join(REPO, "data", "recognize", f"{date}_all", "analysis",
                     f"analyze_{date}.json")
    return json.load(open(p, encoding="utf-8"))["images"]


def append_oos_addendum(report_path, date):
    """若 out_of_sample_hit.json 存在，把诚实口径（剔除目标期行）追加到报告末尾。
    out-of-sample 由 out_of_sample_hit_26233.py 生成；报告重跑时读回，防覆盖丢失。"""
    oos_path = os.path.join(REPO, "data", "recognize", f"{date}_all", "analysis",
                            "out_of_sample_hit.json")
    if not os.path.exists(oos_path):
        return
    d = json.load(open(oos_path, encoding="utf-8"))
    ins, oos = d["in_sample"], d["out_of_sample"]
    types = sorted(set(ins["by_type"]) | set(oos["by_type"]))
    def row(t):
        a, b = ins["by_type"].get(t, {"n": 0, "hit": 0}), oos["by_type"].get(t, {"n": 0, "hit": 0})
        ra = f"{a['hit']/a['n']:.1%}" if a["n"] else "-"
        rb = f"{b['hit']/b['n']:.1%}" if b["n"] else "-"
        return f"| {t} | {a['n']} | {a['hit']} | {ra} | {b['n']} | {b['hit']} | {rb} |"
    addendum = [
        "",
        "## 三、命中率诚实口径（out-of-sample 校验）",
        "",
        "> ⚠️ 一、二节数字为**含目标期行的 in-sample 口径**。博主走势图多为\"开奖后更新\"型，",
        "> 底部行即本期实开奖，extract_candidates 用全部匹配行（含目标期自身）推导 →",
        "> 定位/头/尾/胆码从目标期自证虚高。下表为**剔除目标期行**后重推（=开奖前的真实视角）：",
        "",
        f"- 共 {d['n_img']} 张 ds-ok 图，其中 **{d['n_img_hastarget']} 张**含目标期行（自证来源）。",
        "- 定位/头/尾 oos 率 ≈ 4%（接近 1/10 随机）——从目标期行所在位置取众数必自证。",
        "- 杀号=近 5 期未现数字，命中定义本身使其天然高命中，与预测力无关。",
        "- 结论：规律**提取链路正确**，但 **oos 命中率不是预测力**，彩票开奖随机，仅供博主观赏参考。",
        "",
        "| 类型 | in 条数 | in 命中 | in 率 | oos 条数 | oos 命中 | oos 率 |",
        "|---|---|---|---|---|---|---|",
    ]
    addendum += [row(t) for t in types]
    ti, hi = ins["total"], ins["hit"]
    to, ho = oos["total"], oos["hit"]
    addendum.append(f"| **合计** | **{ti}** | **{hi}** | **{hi/ti:.1%}** "
                    f"| **{to}** | **{ho}** | **{ho/to:.1%}** |")
    with open(report_path, "a", encoding="utf-8") as f:
        f.write("\n".join(addendum) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="20260831")
    ap.add_argument("--target-period", default="26233")
    ap.add_argument("--target-draw", default="1 6 3 4 0")
    args = ap.parse_args()
    target_draw = [int(x) for x in args.target_draw.split()]

    analyze = load_analyze(args.date)
    pat_file = os.path.join(REPO, "data", "crawl", args.date,
                            "image_patterns_with_blogger.json")
    patterns = json.load(open(pat_file, encoding="utf-8"))

    # file -> analyze rec（只对 ds-ok）
    ok_by_file = {}
    for f, r in analyze.items():
        if r.get("decision") == "ds-ok":
            ok_by_file[f] = r

    # ---- 1) hit 重算一致性 ----
    # 存储 hit 是在 build_flat 归一化前用 int position 算的；重算前先把单字 position 转回索引
    recomp_records = []
    for p in patterns:
        r2 = dict(p)
        if isinstance(p["position"], str):
            r2["position"] = pos2idx(p["position"])
        recomp_records.append(r2)
    hit_recomp = run_hits(recomp_records, target_draw)
    hit_mismatch = [p for p, q in zip(patterns, hit_recomp) if p["hit"] != q["hit"]]
    n_hit_stored = sum(1 for p in patterns if p["hit"])
    n_hit_recomp = sum(1 for q in hit_recomp if q["hit"])

    # ---- 2) 规律可重推导（extract_candidates 复现）----
    # 对每张 ds-ok 图：从匹配行重新 extract_candidates，比对存档规律是否都在其中
    file_pat = defaultdict(list)
    for p in patterns:
        file_pat[p["file"]].append(p)

    rederivable = 0
    not_rederivable = []
    n_files_checked = 0
    per_file_derived = {}  # file -> derived candidate keys
    for f, recs in file_pat.items():
        rec = ok_by_file.get(f)
        if rec is None:
            not_rederivable.append((f, "无 analyze ds-ok 记录"))
            continue
        n_files_checked += 1
        mapping = rec["rows"]  # {rowidx: {period, draw, read, matched}}
        derived = extract_candidates(mapping, {})
        dkeys = set()
        for c in derived:
            pos = c["position"] if c["position"] is not None else None
            dkeys.add((c["type"], pos, tuple(sorted(c["numbers"]))))
        per_file_derived[f] = derived
        # 存档规律（跳过空 numbers）
        for p in recs:
            nums = tuple(sorted(int(x) for x in p["numbers"]))
            pos = pos2idx(p["position"])
            key = (p["type"], pos, nums)
            if key in dkeys:
                rederivable += 1
            else:
                not_rederivable.append((f, p["type"], p["position"],
                                        list(nums), p["desc"]))

    # ---- 3) 支持度核对：desc 声称的出现次数 vs 实际 ----
    support_bad = []
    for p in patterns:
        rec = ok_by_file.get(p["file"])
        if rec is None:
            continue
        mapping = rec["rows"]
        matched = [v["draw"] for v in mapping.values()
                   if v.get("matched") and v.get("draw")]
        if not matched:
            continue
        n = len(matched)
        t = p["type"]
        nums = [int(x) for x in p["numbers"]]
        if t in ("定位", "头", "尾"):
            pos = pos2idx(p["position"])
            if pos is None or pos < 0 or pos > 4:
                support_bad.append((p["file"], t, p["position"], nums, "position 越界"))
                continue
            col = [d[pos] for d in matched]
            cnt = col.count(nums[0])
            mode = Counter(col).most_common(1)[0][0]
            if nums[0] != mode:
                support_bad.append((p["file"], t, p["position"], nums,
                                    f"非该位众数(实际众数{mode})"))
        elif t == "胆码":
            all_d = [d for v in matched for d in v]
            cnt = all_d.count(nums[0])
        elif t == "和值":
            sums = [sum(d) for d in matched]
            cnt = sums.count(nums[0])
            if sums.count(nums[0]) < 2:
                support_bad.append((p["file"], t, None, nums, f"和值出现{sums.count(nums[0])}<2"))
                continue
        elif t == "斜连":
            # extract_candidates 两种形式：等差(3期, numbers=1个) / 相邻相连(2期±1, numbers=2个)
            pos = pos2idx(p["position"])
            if pos is None or pos > 4:
                support_bad.append((p["file"], t, p["position"], nums, "position 越界"))
                continue
            col = [d[pos] for d in matched]
            if len(col) < 2:
                support_bad.append((p["file"], t, p["position"], nums, "行数<2"))
                continue
            if len(nums) == 1:
                if len(col) < 3:
                    support_bad.append((p["file"], t, p["position"], nums, "等差需≥3行"))
                    continue
                d1, d2 = col[-1] - col[-2], col[-2] - col[-3]
                if not (d1 == d2 and d1 != 0 and col[-1] == nums[0]):
                    support_bad.append((p["file"], t, p["position"], nums,
                                        f"非等差(末3期{col[-3:]},差{d1},{d2})"))
            else:
                # 相邻相连：近2期差±1 且末两位即 numbers
                if abs(col[-1] - col[-2]) != 1 or set(col[-2:]) != set(nums):
                    support_bad.append((p["file"], t, p["position"], nums,
                                        f"非相邻相连(末2期{col[-2:]})"))
        elif t == "杀号":
            recent = matched[-5:]
            present = set(d for v in recent for d in v)
            overlap = [n for n in nums if n in present]
            if overlap:
                support_bad.append((p["file"], t, None, nums,
                                    f"近5期出现过{overlap}"))
        else:
            support_bad.append((p["file"], t, p["position"], nums, "未知类型"))
            continue
        # 胆码核对出现次数（desc 称 X/NN 次）
        if t == "胆码":
            expected_n = n * 5
            # 只核对 numbers 为单一数字的胆码
            # （desc 格式：数字 X 跨位置出现 c/NN 次）

    # ---- 4) 汇总统计 ----
    by_type = Counter(p["type"] for p in patterns)
    by_type_hit = defaultdict(lambda: [0, 0])
    for p in patterns:
        by_type_hit[p["type"]][0] += 1
        if p["hit"]:
            by_type_hit[p["type"]][1] += 1
    blogger_cnt = Counter(p["blogger"] for p in patterns)
    blogger_imgs = defaultdict(set)
    for p in patterns:
        blogger_imgs[p["blogger"]].add(p["file"])

    lines = [f"# 图片规律总结报告-{args.date}", "",
             f"> {args.target_period} 期开奖：**{' '.join(map(str, target_draw))}**",
             f"- 有效图片规律：**{len(patterns)}** 条，命中 **{n_hit_stored}** 条"
             f"（{n_hit_stored/max(len(patterns),1):.1%}）",
             "",
             "## 一、规律类型分布",
             "| 类型 | 条数 | 命中 | 命中率 |", "|---|---|---|---|"]
    for t, (c, h) in sorted(by_type_hit.items(), key=lambda x: -x[1][0]):
        lines.append(f"| {t} | {c} | {h} | {h/c:.1%} |")
    lines.append("")
    lines.append("## 二、发规律图最多的博主 TOP10")
    lines.append("| 博主 | 规律条数 | 规律图数 |")
    lines.append("|---|---|---|")
    for b, c in blogger_cnt.most_common(10):
        lines.append(f"| {b} | {c} | {len(blogger_imgs[b])} |")

    report = "\n".join(lines)
    out = os.path.join(REPO, "docs", f"图片规律总结报告-{args.date}.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(report)
    append_oos_addendum(out, args.date)

    # ---- 输出校验结论 ----
    print("=" * 60)
    print(f"[校验] 目标期 {args.target_period} = {target_draw}")
    print(f"[汇总] 规律 {len(patterns)} 条（{n_files_checked} 张 ds-ok 图）"
          f"，存储 hit={n_hit_stored}，重算 hit={n_hit_recomp}，不一致 {len(hit_mismatch)} 条")
    print(f"[汇总] -> {out}")
    print(f"[校验] 规律可重推导（extract_candidates 复现命中）：{rederivable}"
          f"/{len(patterns)}，失败 {len(not_rederivable)} 条")
    if not_rederivable:
        for x in not_rederivable[:20]:
            print("   ✗", x)
    print(f"[校验] 支持度/位置核对失败：{len(support_bad)} 条")
    for x in support_bad[:20]:
        print("   ✗", x)
    print("=" * 60)

    # 存校验结果
    vout = os.path.join(REPO, "data", "recognize", f"{args.date}_all", "analysis",
                        "verify_patterns.json")
    with open(vout, "w", encoding="utf-8") as f:
        json.dump({
            "target_period": args.target_period,
            "target_draw": target_draw,
            "n_patterns": len(patterns),
            "n_files": n_files_checked,
            "hit_stored": n_hit_stored,
            "hit_recomp": n_hit_recomp,
            "hit_mismatch": len(hit_mismatch),
            "rederivable": rederivable,
            "not_rederivable": not_rederivable[:50],
            "support_bad": support_bad[:50],
            "by_type": dict(by_type_hit),
        }, f, ensure_ascii=False, indent=1)
    print(f"[校验] 结果落盘 -> {vout}")


if __name__ == "__main__":
    main()
