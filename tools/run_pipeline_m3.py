#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
M3: 规律池接入框架 Analyzer→Decision 流水线。
- 对 409 条规律做历史回测（最近 40 期真实开奖）计算命中率
- 构造 Pattern → DecisionAgent 打分（近期0.5/全局0.3/支持度0.2）
- 定位类冲突消解 → TopN 启用 → 预测组合输出
输入: data/crawl/20260828/{pattern_pool.json, lottery_recent.json}
输出: docs/M3决策流水线报告-20260828.md
"""
import hashlib
import itertools
import json
import os
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
BASE = os.path.join(REPO, "data", "crawl", "20260828")

from models.schemas import (Pattern, PatternPool, DecisionResult, SelectedPattern,
                            DiscardedPattern, PatternStatus, HistoryRecord)
from agents.decision import DecisionAgent
from memory.vcp import VCPMemory

POS_NAMES = {0: "万位", 1: "千位", 2: "百位", 3: "十位", 4: "个位"}


def load_history():
    lottery = json.load(open(os.path.join(BASE, "lottery_recent.json"), encoding="utf-8"))
    return [HistoryRecord(period=r["period"], numbers=r["numbers"], date=r["date"]) for r in lottery]


def check_hit(it, d):
    t = it["type"]
    nums = it["numbers"]
    pos = it["position"]
    if t == "杀号":
        return all(n not in d for n in nums)
    if t in ("定位", "头", "尾"):
        if pos is None:
            return False
        return d[pos] in nums
    if t == "数字串":
        return bool(set(nums) & set(d))
    if t == "和值":
        s = sum(d)
        cand = set(nums)
        for a in nums:
            for b in nums:
                cand.add(a * 10 + b)
        return s in cand
    return False


def describe(it):
    t, pos, nums = it["type"], it["position"], it["numbers"]
    ns = ",".join(map(str, nums))
    if t == "定位":
        return f"定位-{POS_NAMES.get(pos, '?')}={ns}"
    if t == "杀号":
        return f"杀号-杀{ns}"
    if t == "头":
        return f"头={ns}"
    if t == "尾":
        return f"尾={ns}"
    if t == "和值":
        return f"和值候选{ns}"
    if t == "数字串":
        return f"数字串{ns}"
    return f"{t}-{ns}"


def positional_conflict_resolve(scored):
    """定位类冲突消解：同位置单数字互斥 / 杀号与定位互斥，保留得分高者"""
    discarded = []
    resolved = []
    discarded_ids = set()

    for i, (p1, s1) in enumerate(scored):
        if p1.pattern_id in discarded_ids:
            continue
        for j, (p2, s2) in enumerate(scored):
            if i >= j or p2.pattern_id in discarded_ids:
                continue
            conflict = False
            # 同位置、单数字、且数字不同 → 互斥
            if (p1.position is not None and p1.position == p2.position
                    and len(p1.numbers) == 1 and len(p2.numbers) == 1
                    and p1.numbers != p2.numbers):
                conflict = True
            # 杀号 vs 定位：杀的数字被定位推 → 互斥
            if p1.type == "杀号" and p2.type in ("定位", "头", "尾"):
                if any(n in p2.numbers for n in p1.numbers):
                    conflict = True
            if conflict:
                if s1 >= s2:
                    discarded.append(DiscardedPattern(pattern=p2, reason=f"与「{p1.description[:20]}」互斥，得分更低({s2}<{s1})"))
                    discarded_ids.add(p2.pattern_id)
                else:
                    discarded.append(DiscardedPattern(pattern=p1, reason=f"与「{p2.description[:20]}」互斥，得分更低({s1}<{s2})"))
                    discarded_ids.add(p1.pattern_id)
                    break
        if p1.pattern_id not in discarded_ids:
            resolved.append((p1, s1))
    return resolved, discarded


def main():
    pool = json.load(open(os.path.join(BASE, "pattern_pool.json"), encoding="utf-8"))
    # 保险：按完整 key（类型|位置|数字）再合并一次
    merged = {}
    for it in pool:
        if it["type"] == "杀号":
            it["position"] = None  # 杀号统一无位置语义，避免拆分
        key = (it["type"], it["position"], tuple(it["numbers"]))
        if key in merged:
            merged[key]["support"] += it["support"]
        else:
            merged[key] = dict(it)
    pool = list(merged.values())
    history = load_history()
    recent_n = 10
    print("规律池:", len(pool), "| 历史期数:", len(history))

    patterns = []
    for it in pool:
        if it["type"] == "数字串":
            continue  # 无位置语义，排除出主决策
        hits = misses = 0
        rhits = rmisses = 0
        for i, h in enumerate(history):
            ok = check_hit(it, h.numbers)
            if ok:
                hits += 1
                if i < recent_n:
                    rhits += 1
            else:
                misses += 1
                if i < recent_n:
                    rmisses += 1
        desc = describe(it)
        pat = Pattern(
            pattern_id=hashlib.md5(
                f"{it['type']}|{it['position']}|{it['numbers']}".encode()).hexdigest()[:16],
            description=desc,
            source_authors=["论坛博主"],
            support_count=it["support"],
            global_hits=hits, global_misses=misses,
            recent_hits=rhits, recent_misses=rmisses,
            evidence=[f"回测{len(history)}期", f"26230命中={it.get('hit_26230')}"],
        )
        # 挂上结构化字段供冲突消解用
        pat.type = it["type"]
        pat.position = it["position"]
        pat.numbers = it["numbers"]
        patterns.append(pat)

    vcp = VCPMemory(storage_path=os.path.join(BASE, "vcp_m3_tmp.json"))
    da = DecisionAgent(vcp, max_selected=8)

    # 黑名单：历史命中率 < 0.1 的规律直接过滤（近似失效池）
    blacklist = [p for p in patterns if p.global_accuracy < 0.1]
    remaining = [p for p in patterns if p.global_accuracy >= 0.1]
    black_ids = {p.pattern_id for p in blacklist}
    print(f"黑名单(历史命中率<10%): {len(blacklist)} | 剩余: {len(remaining)}")

    scored = da._score_patterns(remaining)
    resolved, discarded_c = positional_conflict_resolve(scored)
    selected, discarded_low = da._select_top_n(resolved)

    discarded_black = [DiscardedPattern(pattern=p, reason=f"历史命中率过低({p.global_accuracy:.0%})")
                       for p in blacklist]
    result = DecisionResult(
        selected=selected,
        discarded=discarded_black + discarded_c + discarded_low,
        observing=[p for p in remaining if p.support_count < 3],
        period="26231",
    )

    # 预测文本（自定义方向）
    parts = []
    for sp in selected:
        p = sp.pattern
        if p.type == "定位" and p.position is not None:
            parts.append(f"[{sp.weight:.0%}] {POS_NAMES[p.position]}看好{','.join(map(str, p.numbers))}")
        elif p.type == "杀号":
            parts.append(f"[{sp.weight:.0%}] 杀{','.join(map(str, p.numbers))}")
        elif p.type == "头":
            parts.append(f"[{sp.weight:.0%}] 头看好{','.join(map(str, p.numbers))}")
        elif p.type == "尾":
            parts.append(f"[{sp.weight:.0%}] 尾看好{','.join(map(str, p.numbers))}")
        else:
            parts.append(f"[{sp.weight:.0%}] {p.description}")
    prediction_text = "；".join(parts)
    confidence = sum(sp.weight * sp.score for sp in selected)

    # 报告
    lines = ["# M3 决策流水线报告（基于 2026-08-28 规律池）", "",
             f"> 规律池 {len(pool)} 条 → 历史回测(近{len(history)}期) → 打分/消解 → 启用 {len(selected)} 条",
             f"> 目标期：26231（沿用 8/28 博主规律，仅供参考）", "",
             "## 预测组合", f"**{prediction_text}**", f"置信度：**{confidence:.1%}**", "",
             "## 启用规律", "| 规律 | 支持 | 近10期命中 | 全局命中 | 得分 | 权重 |", "|---|---|---|---|---|---|"]
    for sp in sorted(selected, key=lambda x: -x.weight):
        p = sp.pattern
        lines.append(f"| {p.description} | {p.support_count} | {p.recent_accuracy:.0%} | {p.global_accuracy:.0%} | {sp.score:.3f} | {sp.weight:.0%} |")
    lines.append("")
    lines.append(f"## 丢弃统计：黑名单 {len(discarded_black)} + 冲突 {len(discarded_c)} + 排名外 {len(discarded_low)} = {len(result.discarded)}")
    lines.append("")
    lines.append("## 冲突消解示例（Top5）")
    for dp in discarded_c[:5]:
        lines.append(f"- ❌ {dp.pattern.description}（{dp.reason}）")
    lines.append("")
    lines.append("## 说明")
    lines.append("- 历史命中率为真实开奖回测（含 26230 期）；数字串类命中率虚高，其权重被冲突规则部分抑制")
    lines.append("- 预测仅演示流水线，彩票随机，请勿用于投注")
    report = "\n".join(lines)

    out = os.path.join(REPO, "docs", "M3决策流水线报告-20260828.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)


if __name__ == "__main__":
    main()
