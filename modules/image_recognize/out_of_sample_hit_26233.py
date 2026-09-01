#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""out_of_sample_hit_26233.py — 命中率 in-sample vs out-of-sample 对比。

问题：博主走势图是"开奖后更新"型，底部行即 26233 实开奖。extract_candidates 用
全部匹配行（含 26233 目标行）推导 → hit 有自证（in-sample）虚高。

本脚本对每张 ds-ok 图：
  1. in-sample：现有逻辑（含 26233 行）extract_candidates → run_hits(26233)
  2. out-of-sample：剔除 26233 行后 extract_candidates → run_hits(26233)
对比两类命中率，给出诚实的"前瞻"口径。

用法:
  /usr/bin/python3 modules/image_recognize/out_of_sample_hit_26233.py \
      --date 20260831 --target-period 26233 --target-draw "1 6 3 4 0"
"""
import argparse
import json
import os
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "modules", "image_recognize"))
from common import run_hits, load_json  # noqa: E402
from stage4_llm import extract_candidates  # noqa: E402


def summarize(hits):
    n = len(hits)
    h = sum(1 for x in hits if x)
    return n, h, (h / n if n else 0.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="20260831")
    ap.add_argument("--target-period", default="26233")
    ap.add_argument("--target-draw", default="1 6 3 4 0")
    args = ap.parse_args()
    target_draw = [int(x) for x in args.target_draw.split()]
    target = int(args.target_period)

    an = load_json(os.path.join(REPO, "data", "recognize", f"{args.date}_all",
                                "analysis", f"analyze_{args.date}.json"))
    imgs = an["images"]

    by_type_ins = Counter()
    by_type_ins_hit = Counter()
    by_type_oos = Counter()
    by_type_oos_hit = Counter()
    n_img_hastarget = 0
    n_img = 0
    n_img_zero_ins = n_img_zero_oos = 0

    for f, r in imgs.items():
        if r.get("decision") != "ds-ok":
            continue
        n_img += 1
        rows = r.get("rows") or {}
        matched = {int(i): v for i, v in rows.items()
                   if v.get("matched") and v.get("draw") and v.get("period")}
        if any(int(v["period"]) == target for v in matched.values()):
            n_img_hastarget += 1
            # out-of-sample：剔除目标期行
            oos_rows = {i: v for i, v in matched.items()
                        if int(v["period"]) != target}
        else:
            oos_rows = matched

        # in-sample（现有逻辑：全匹配行）
        ins_cands = [{"type": c["type"], "position": c.get("position"),
                      "numbers": c["numbers"]} for c in extract_candidates(matched, {})]
        ins_hits = run_hits(ins_cands, target_draw) if ins_cands else []
        if not ins_cands:
            n_img_zero_ins += 1
        # out-of-sample
        oos_cands = [{"type": c["type"], "position": c.get("position"),
                      "numbers": c["numbers"]} for c in extract_candidates(oos_rows, {})]
        oos_hits = run_hits(oos_cands, target_draw) if oos_cands else []
        if not oos_cands:
            n_img_zero_oos += 1

        for c, h in zip(ins_cands, ins_hits):
            by_type_ins[c["type"]] += 1
            if h["hit"]:
                by_type_ins_hit[c["type"]] += 1
        for c, h in zip(oos_cands, oos_hits):
            by_type_oos[c["type"]] += 1
            if h["hit"]:
                by_type_oos_hit[c["type"]] += 1

    print("=" * 66)
    print(f"[{args.target_period}={target_draw}]  ds-ok 图 {n_img} 张，"
          f"其中含目标期行 {n_img_hastarget} 张（自证来源），"
          f"无候选图 in-sample {n_img_zero_ins} / out-of-sample {n_img_zero_oos}")
    print(f"\n{'类型':<6}{'in条数':>7}{'in命中':>8}{'in率':>8}"
          f"{'oos条数':>9}{'oos命中':>9}{'oos率':>8}")
    all_types = set(by_type_ins) | set(by_type_oos)
    for t in sorted(all_types):
        ni, hi, ri = summarize([by_type_ins_hit[t]] * 1 if False else
                               [True]*by_type_ins_hit[t] + [False]*(by_type_ins[t]-by_type_ins_hit[t]))
        no, ho, ro = summarize([True]*by_type_oos_hit[t] + [False]*(by_type_oos[t]-by_type_oos_hit[t]))
        print(f"{t:<6}{ni:>7}{hi:>8}{ri:>8.1%}{no:>9}{ho:>9}{ro:>8.1%}")
    nt, ht, rt = summarize([True]*sum(by_type_ins_hit.values()) +
                           [False]*(sum(by_type_ins.values())-sum(by_type_ins_hit.values())))
    no, ho, ro = summarize([True]*sum(by_type_oos_hit.values()) +
                           [False]*(sum(by_type_oos.values())-sum(by_type_oos_hit.values())))
    print(f"{'合计':<6}{nt:>7}{ht:>8}{rt:>8.1%}{no:>9}{ho:>9}{ro:>8.1%}")
    print("=" * 66)

    out = os.path.join(REPO, "data", "recognize", f"{args.date}_all", "analysis",
                       "out_of_sample_hit.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"target_period": args.target_period, "target_draw": target_draw,
                   "n_img": n_img, "n_img_hastarget": n_img_hastarget,
                   "in_sample": {"total": sum(by_type_ins.values()),
                                 "hit": sum(by_type_ins_hit.values()),
                                 "by_type": {t: {"n": by_type_ins[t], "hit": by_type_ins_hit[t]}
                                             for t in by_type_ins}},
                   "out_of_sample": {"total": sum(by_type_oos.values()),
                                     "hit": sum(by_type_oos_hit.values()),
                                     "by_type": {t: {"n": by_type_oos[t], "hit": by_type_oos_hit[t]}
                                                 for t in by_type_oos}}},
                  f, ensure_ascii=False, indent=1)
    print(f"结果落盘 -> {out}")


if __name__ == "__main__":
    main()
