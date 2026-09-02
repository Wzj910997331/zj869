#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
纯计算复测（不接视觉模型）— 2026-09-01

对 image_patterns_with_blogger.json 中每条命中记录，用识别结果里的
type/position/numbers 推出博主本期预测的位置与候选数字，逐位置对照实际开奖打 ✓/✗，
统计 multi（如 "1位置1中"/"胆码全盘3中"），并给 logic 补一句推演说明。
输出结构与 glm_multipos_recheck.json 兼容（apply_multipos_recheck.py 可直接消费）。

同时用 img_type 标记"报号/铁率"候选（文字预测截图类，博主只报数字无画规）——
这些推演得出来但不体现画规，是否剔除由人工定夺（见 --review 输出）。

用法:
  python tools/recheck_compute.py --base data/crawl/20260829 --period 26231 \
      --draw "1 8 7 9 9" --calib 26230 --calib-draw "9 4 6 8 3"
"""
import argparse
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POS_NAME = ["万", "千", "百", "十", "个"]


def norm_pos(pm):
    """position 归一化：返回 (字符串, 位置索引列表 0-4 对应 万千百十个)。"""
    if not pm:
        return None, []
    s = str(pm).replace("位", "").strip()
    parts = [p for p in re.split(r"[-~到至→,、/]", s) if p]
    idxs = []
    for p in parts:
        if p in POS_NAME:
            idxs.append(POS_NAME.index(p))
    return s, sorted(set(idxs))


def derive(rec, actual):
    """由识别结果推出预测位置与每位置对错。返回 (predicted_positions, pos_check, multi)。"""
    t = rec.get("type")
    nums = rec.get("numbers") or []
    pos_s, idxs = norm_pos(rec.get("position"))
    pp = []
    pc = {}
    if t == "胆码" or (not idxs and t not in ("斜连", "和值", "杀号")):
        # 胆码（或全盘无位置）→ 5 位全盘候选
        for i in range(5):
            pp.append({"位置": POS_NAME[i], "候选": nums,
                       "标注方式": "胆码全盘", "原文": f"胆{','.join(map(str, nums))}"})
        n = sum(1 for i in range(5) if actual[i] in nums)
        multi = f"胆码全盘{n}中"
    elif idxs:
        for i in idxs:
            pp.append({"位置": POS_NAME[i], "候选": nums,
                       "标注方式": {"定位": "定位标注", "头": "头位", "尾": "尾位",
                                    "斜连": "斜连", "和值": "和值"}.get(t, t or "预测"),
                       "原文": f"{POS_NAME[i]}{','.join(map(str, nums))}"})
        n = sum(1 for i in idxs if actual[i] in nums)
        multi = f"{len(idxs)}位置{n}中"
    else:
        # 无位置且非胆码（和值/斜连无位置）→ 无法逐位置推演
        if t == "和值":
            s = sum(actual)
            hit = s in nums
            return [{"位置": "和值", "候选": nums, "标注方式": "和值", "原文": f"和值∈{nums}"}], \
                   {"和值": f"实际和值{s} {('含' if hit else '不含')}候选{','.join(map(str, nums))} {'✓' if hit else '✗'}"}, \
                   f"和值{hit and 1 or 0}中"
        return [], {}, "无法推演"
    for p in pp:
        po = POS_NAME.index(p["位置"])
        ok = actual[po] in nums
        pc[p["位置"]] = f"候选[{','.join(map(str, nums))}] {('含' if ok else '不含')}实际{actual[po]} {'✓' if ok else '✗'}"
    return pp, pc, multi


def build_logic(rec, pp, pc, multi):
    t = rec.get("type")
    nums = rec.get("numbers") or []
    pos = rec.get("position")
    n_ok = sum(1 for v in pc.values() if "✓" in v)
    if t == "胆码":
        return f"博主胆码 {','.join(map(str, nums))}（全盘）：开奖 {','.join(POS_NAME[i] for i in range(5) if pc[POS_NAME[i]].count('✓'))}含 {n_ok} 个候选数字 → {multi}"
    pos_txt = f"{pos}位" if pos else "全盘"
    return f"博主预测 {t} {pos_txt}={','.join(map(str, nums))}；对位校验 {'；'.join(pc.values())} → {multi}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="data/crawl/YYYYMMDD")
    ap.add_argument("--period", required=True)
    ap.add_argument("--draw", required=True, help="本期开奖，如 '1 8 7 9 9'")
    ap.add_argument("--calib", default="", help="校准期号（仅写入说明）")
    ap.add_argument("--calib-draw", default="", help="校准期开奖")
    args = ap.parse_args()

    BASE = os.path.join(REPO, "data", "crawl", args.base.replace("data/crawl/", ""))
    PATH = os.path.join(BASE, "image_patterns_with_blogger.json")
    OUT = os.path.join(BASE, "glm_multipos_recheck.json")

    actual = [int(x) for x in args.draw.split()]
    recs = json.load(open(PATH, encoding="utf-8"))
    hits = [r for r in recs if r.get("hit") and r.get("type") != "杀号"]

    hits_out, rejected, no_deriv = [], [], []
    review = []  # 报号/铁率候选（推演得出来但 img_type=文字预测截图）
    for r in hits:
        pp, pc, multi = derive(r, actual)
        if not pp:
            no_deriv.append({"file": r.get("file"), "blogger": r.get("blogger"),
                             "type": r.get("type"), "position": r.get("position"),
                             "reason": f"无位置且非胆码，无法逐位置推演（type={r.get('type')}）"})
            continue
        entry = {
            "file": r.get("file"),
            "blogger": r.get("blogger"),
            "predicted_positions": pp,
            "pos_check": pc,
            "logic": build_logic(r, pp, pc, multi),
            "multi": multi,
            "img_type": r.get("img_type"),
            "desc": r.get("desc"),
            "type": r.get("type"),
        }
        hits_out.append(entry)
        if r.get("img_type") == "文字预测截图":
            review.append(entry)

    out = {
        "说明": f"纯计算复测（无视觉模型）{args.period} 期命中记录 "
                f"(calib {args.calib}={args.calib_draw})；用识别结果 type/position/numbers 推出预测逐位置对位",
        "actual": actual,
        "hits": hits_out,
        "rejected": no_deriv,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    # ---- 汇总 ----
    from collections import Counter
    multi_c = Counter(h["multi"] for h in hits_out)
    print(f"命中记录 {len(hits)} 条 → 可推演 {len(hits_out)} / 无法推演 {len(no_deriv)}")
    print("multi 分布:", dict(multi_c))
    print("报号/铁率候选(文字预测截图):", len(review), "条")
    for e in review:
        print(f"  ⚠️ {e['blogger']} [{e['type']}] {e['desc'][:40]}…")
    if no_deriv:
        print("无法推演:")
        for e in no_deriv:
            print(f"  ✗ {e['blogger']} [{e['type']}] pos={e['position']}: {e['reason']}")
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
