#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backtest_calc_rules_26233.py — 机器内"简单计算规律"前瞻回测。

博主在某期开奖前用简单计算(和值/胆码/定位/斜连等差/头尾/杀号)给出预测。
本脚本完全复刻这套计算,但严格只用"某期之前"的历史开奖(无自证):
  对每个历史期 P,取 P 之前最近 W 期 → extract_candidates 生成候选 →
  对 P 实际开奖 run_hits 判命中 → 统计每类规律的长期命中率 vs 随机基线。

输入: data/crawl/20260831/lottery_recent.json (60期, 26174..26233, 最新在前)
输出: data/recognize/20260831_all/analysis/backtest_calc.json

用法:
  /usr/bin/python3 modules/image_recognize/backtest_calc_rules_26233.py \
      --start 26200 --end 26233 --window 12
"""
import argparse
import json
import os
import sys
from collections import Counter, defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "modules", "image_recognize"))
from common import run_hits, load_json  # noqa: E402
from stage4_llm import extract_candidates  # noqa: E402

# 随机基线参考(单数字命中该位=10%;胆码=该数字出现在5位任一≈41%)
BASE = {"定位": 0.10, "头": 0.10, "尾": 0.10, "斜连": 0.10, "胆码": 0.41}


def build_rows(hist_prev, window):
    """把某期之前最近的 window 期构造成 mapping(最旧在顶=inc,与博主图同构)。"""
    rows = {}
    for i, p in enumerate(hist_prev[-window:]):
        rows[i] = {"period": p["period"], "draw": p["numbers"],
                   "read": p["numbers"], "matched": True}
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lottery", default=os.path.join(
        REPO, "data", "crawl", "20260831", "lottery_recent.json"))
    ap.add_argument("--start", type=int, default=26200)
    ap.add_argument("--end", type=int, default=26233)
    ap.add_argument("--window", type=int, default=12)
    args = ap.parse_args()

    lot = load_json(args.lottery)  # 最新在前
    byp = {p["period"]: p["numbers"] for p in lot}
    seq = [p for p in lot if int(p["period"]) <= args.end]
    seq = seq[::-1]  # 升序(旧→新)
    periods = [int(p["period"]) for p in seq]

    by_type = defaultdict(lambda: [0, 0])   # type -> [n_cand, n_hit]
    per_period = {}
    focus = {}

    for idx, p in enumerate(seq):
        P = int(p["period"])
        if P < args.start:
            continue
        if idx < args.window:   # 期前历史不足窗口
            continue
        hist_prev = seq[:idx]   # 严格只用 P 之前
        mapping = build_rows(hist_prev, args.window)
        cands = [{"type": c["type"], "position": c.get("position"),
                  "numbers": c["numbers"], "desc": c.get("desc")}
                 for c in extract_candidates(mapping, {})]
        hits = run_hits(cands, p["numbers"])
        rec = []
        for c, h in zip(cands, hits):
            by_type[c["type"]][0] += 1
            if h["hit"]:
                by_type[c["type"]][1] += 1
            rec.append({"type": c["type"], "position": c.get("position"),
                        "numbers": c["numbers"], "hit": h["hit"],
                        "desc": c["desc"]})
        per_period[str(P)] = {"draw": p["numbers"], "n_cand": len(rec),
                              "n_hit": sum(1 for r in rec if r["hit"]),
                              "patterns": rec}
        if P in (26229, 26230):
            focus[str(P)] = per_period[str(P)]

    # 汇总
    print("=" * 70)
    print(f"[回测] 期 {args.start}..{args.end}，窗口 W={args.window}，"
          f"覆盖 {len(per_period)} 期（每期仅用其之前的历史，无自证）")
    print(f"\n{'类型':<6}{'候选数':>7}{'命中':>6}{'命中率':>9}{'随机基线':>9}{'判定':>8}")
    for t in sorted(by_type):
        n, h = by_type[t]
        r = h / n if n else 0
        b = BASE.get(t)
        judge = "≈随机" if (b is None or abs(r - b) < 0.08) else (
            "↑高于随机" if r > b else "↓低于随机")
        print(f"{t:<6}{n:>7}{h:>6}{r:>9.1%}{str(b or '-'):>9}{judge:>8}")
    tot_n = sum(v[0] for v in by_type.values())
    tot_h = sum(v[1] for v in by_type.values())
    print(f"{'合计':<6}{tot_n:>7}{tot_h:>6}{tot_h/tot_n:>9.1%}")

    print("\n" + "=" * 70)
    print("[用户点名期] 26229 / 26230（用各自之前 12 期算出的规律）")
    for P in ("26229", "26230"):
        rec = focus.get(P)
        if not rec:
            continue
        print(f"\n-- {P} 实际开奖={rec['draw']}  命中 {rec['n_hit']}/{rec['n_cand']} --")
        for r in rec["patterns"]:
            mark = "✓" if r["hit"] else "✗"
            print(f"   [{mark}] {r['type']} pos={r['position']} nums={r['numbers']}  {r['desc']}")

    out = os.path.join(REPO, "data", "recognize", "20260831_all", "analysis",
                       "backtest_calc.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"start": args.start, "end": args.end, "window": args.window,
                   "by_type": {t: {"n": v[0], "hit": v[1]} for t, v in by_type.items()},
                   "per_period": per_period}, f, ensure_ascii=False, indent=1)
    print(f"\n结果落盘 -> {out}")


if __name__ == "__main__":
    main()
