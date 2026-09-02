#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
导出"命中规律库"：从 image_patterns_with_blogger.json 提取本期命中记录，
连同采集条数/命中率，写成 docs/规律/{period}.json + {period}.md。
每期一个文件，规律累计可查。
用法: python tools/export_rules.py --base 20260829 --period 26231 --draw "1 8 7 9 9" --calib 26230 --calib-draw "9 4 6 8 3"
      [--cutoff 21:30]  开奖前发帖过滤（排列5 每日 21:30 开奖；开奖后发帖=复盘/下期预测，剔除；空串禁用）
      [--require-verified]  命中必须经独立二次识读复核；仅单源视觉读数（未复核）的命中候选一律计入剔除清单
      （26231/26232 命中归零即由此参数导出：单源读数同图可解释为任意数字，按口径不计命中）
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


def real_hit(r, actual):
    """最终命中口径：博主在走势图上画出的、有位置的、恰好押 1 个数字、
    且该位置实际真的开出这个数字的预测，才算命中。

    以下一律不算（都不体现可复现画规规律 / 推不出本期唯一结果）：
      - 杀号（杀掉不开出的号码）
      - 报号/铁率（文字预测截图等，博主直接打字报数、无画规）
      - 不定位（无位置：胆码全盘/组合推荐）
      - 多码宽网（≥2 候选，候选池式，无法推出唯一结果）
      - 单码但位置未对上（数字开在别位，只靠全盘碰巧命中）"""
    if not r.get("hit"):
        return False
    if r.get("type") == "杀号":
        return False
    if r.get("img_type") != "走势图圈选":
        return False
    if not r.get("position"):
        return False
    nums = r.get("numbers") or []
    if len(nums) != 1:                    # 多码宽网
        return False
    pos = r.get("position")
    if pos not in actual:                 # 区间/无法对位
        return False
    return actual[pos] == nums[0]         # 单码且该位真实开出


def valid_post_factory(base_dir, cutoff):
    """开奖前发帖过滤：博主在本期开奖(21:30)后才发的图属"复盘/下期预测"，
    图里已含本期开奖结果（博主圈的是已知号码），不能算本期预测。

    posts.json 中每帖带 create_time；图片名 s_2_<post_id>_<n>.jpg 直接对回帖 id。
    cutoff 如 '21:30'：保留发帖时间严格早于该时刻的记录。
    返回 valid(r)；posts.json 缺失 → 不过滤（老期）；记录对不上帖 → 保守剔除。
    """
    if not os.path.exists(os.path.join(base_dir, "posts.json")):
        return lambda r: True  # 老期无 posts.json，不做时间过滤
    def valid(r):
        t = post_time(r, base_dir)
        return bool(t) and t < cutoff
    return valid


def post_time(r, base_dir):
    """返回该记录图片所属帖子的发帖时刻 'HH:MM'，或 ''。"""
    import re as _re
    try:
        posts = json.load(open(os.path.join(base_dir, "posts.json"), encoding="utf-8"))
    except Exception:
        return ""
    pm = {p.get("id"): p.get("create_time", "") for p in posts}
    m = _re.match(r"(s_2_[0-9a-f-]+)_\d+\.(?:jpg|png|jpeg)$", r.get("file") or "")
    if not m:
        return ""
    ct = pm.get(m.group(1), "")
    return ct[11:16] if len(ct) >= 16 else ""


def is_recap(r, base_dir, cutoff):
    """判断该命中候选是否因"开奖后发帖"被剔（复盘/下期预测）。"""
    t = post_time(r, base_dir)
    return bool(t) and t >= cutoff


def reject_reason(r):
    """被剔除的命中候选的剔除原因（优先用已记录的原因，否则按口径推导）。"""
    if r.get("reject_reason"):
        return r.get("reject_reason")
    if r.get("img_type") != "走势图圈选":
        return f"报号/铁率（{r.get('img_type')}）：博主文字报数/缩水推荐，不体现画规"
    if not r.get("position"):
        return "不定位（无位置）：胆码全盘/组合推荐，非定位画规"
    nums = r.get("numbers") or []
    if len(nums) != 1:
        return f"多码宽网（{len(nums)} 码候选）：候选池式，推不出本期唯一结果"
    return "单码但位置未对上：数字开在别位，仅全盘碰巧命中"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="data/crawl/YYYYMMDD")
    ap.add_argument("--period", required=True, help="预测期号，如 26231")
    ap.add_argument("--draw", required=True, help="本期开奖，如 '1 8 7 9 9'")
    ap.add_argument("--calib", default="", help="校准期号，如 26230")
    ap.add_argument("--calib-draw", default="", help="校准期开奖，如 '9 4 6 8 3'")
    ap.add_argument("--cutoff", default="21:30",
                    help="本期开奖前发帖截止时刻 HH:MM（排列5 每日 21:30 开奖；"
                         "发帖晚于此属复盘/下期预测，剔除）。传空串禁用")
    ap.add_argument("--require-verified", action="store_true",
                    help="命中必须经独立二次识读复核（GLM 多位置重读/人工）。开启后，"
                         "凡仅单源视觉识别读数（图上手绘标记未经第二来源确认）的命中候选一律不计，"
                         "避免'同图可解释为任意数字'的不可验证命中")
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
    valid = valid_post_factory(BASE, args.cutoff)
    # 采集口径：仅博主画规（走势图圈选、非杀号）且开奖前发帖；
    # 杀号与报号/铁率不体现画规，整体剔除；开奖后发帖是复盘/下期预测，整体剔除
    kept = [r for r in data if r.get("type") != "杀号" and r.get("img_type") == "走势图圈选"
            and valid(r)]
    hit_cand = [r for r in kept if real_hit(r, ACTUAL)]
    hits = [] if args.require_verified else hit_cand
    # 被剔除的命中候选：识别为命中但不符最终口径（开奖后发帖复盘 / 未独立复核 /
    # 报号铁率、无位置、多码宽网、单码错位）
    rejected = []
    for r in data:
        if not (r.get("hit") and r.get("type") != "杀号"):
            continue
        if args.cutoff and is_recap(r, BASE, args.cutoff):
            t = post_time(r, BASE)
            rejected.append({"blogger": r.get("blogger"),
                             "reason": f"开奖后发帖（复盘/下期预测，发帖 {t}）：晚于本期开奖，"
                                       f"图含本期开奖结果，博主圈的是已知号码"})
        elif args.require_verified and real_hit(r, ACTUAL) and r in hit_cand:
            rejected.append({"blogger": r.get("blogger"),
                             "reason": "未独立二次识读（仅单源视觉识别读数）：图上标记未经第二来源"
                                       "确认，同图可解释为任意数字，按口径不计命中"})
        elif not real_hit(r, ACTUAL):
            rejected.append({"blogger": r.get("blogger"), "reason": reject_reason(r)})
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

    # ---- JSON ----
    rules = []
    for r in hits:
        # 最终口径下每条规则都是"1位置1中"（单码 + 该位真实开出）；无 multi 时补缺省
        multi = r.get("multi")
        if not multi:
            multi = "1位置1中"
        rules.append({
            "period": PERIOD,
            "draw": DRAW,
            "blogger": r.get("blogger"),
            "image": r.get("file"),
            "type": r.get("type"),
            "hit_position": r.get("position"),
            "hit_numbers": r.get("numbers"),
            "multi": multi,
            "predicted_positions": r.get("predicted_positions"),
            "pos_check": r.get("pos_check"),
            "logic": r.get("logic"),
        })
    n_full = sum(1 for rr in rules if rr.get("multi") == "1位置1中")
    out = {
        "period": PERIOD,
        "draw": DRAW,
        "total_records": n_total,
        "hit_records": n_hit,
        "hit_rate": round(hit_rate, 4),
        "full_hits": n_full,
        "rejected": [{"blogger": r.get("blogger"), "reason": r.get("reason")} for r in rejected],
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
        L.append(f"- **{r.get('blogger')}**：{r.get('reason')}")
    L.append("")
    L.append("> 规律为博主手绘画规的历史总结，彩票开奖属独立随机事件，不具备预测效力。")
    with open(os.path.join(OUT_DIR, f"{PERIOD}.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    print(f"规律库 → {OUT_DIR}/")
    print(f"  采集 {n_total} 条 / 命中 {n_hit} ({hit_rate:.2%}) / 完全命中 {n_full} / 规律 {len(rules)} 条")
    print(f"  剔除 {len(rejected)} 条: {[r.get('blogger') for r in rejected]}")


if __name__ == "__main__":
    main()
