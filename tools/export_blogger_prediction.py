#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""export_blogger_prediction.py — 导出新版命中规律库（博主目标期行手写 + 单押口径）。

吃 verify_blogger_prediction 的 blogger_predictions_verify.json（逐位置分类 hit/miss/wide/noloc），
导出 docs/规律/{period}.json + .md。schema 与旧 export_rules 的 26230.json 一致：
  {period, draw, total_records, hit_records, hit_rate, full_hits, rejected, rules, rule_count}

词表：
  采集 total_records = 该期博主「一位只写 1 个数」且能定位的预测位置条数（单押单码）。
      多码/和值(≥2码)、不定位、以及 没写数字的空读图(纯圈/线) 都剔除 —— 它们不是单押，
      命中率应只以单码采集为基准。缺窄条/读图失败的图也不计入。
  命中 hit_records  = 其中「单押 1 码 + 位对 + 该位实开该数」的条数。
  rejected          = 被剔除的预测位置（多码宽网(C)/不定位/空读(B) + 图级剔除），按博主去重。

用法:
  python3 tools/export_blogger_prediction.py \
      --verify data/crawl/20260829/blogger_predictions_verify.json \
      --period 26231 --draw "1 8 7 9 9"
"""
import argparse
import json
import os
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(REPO, "docs", "规律")


def norm_pos(s):
    return str(s).replace("位", "").strip() if s else s


def pos_why(cls):
    return {"wide": "多码宽网(≥2 候选)：候选池式，推不出本期唯一结果",
            "noloc": "不定位（无位置/无法对位）：非定位画规",
            "miss": "单码错位/未中：博主押的单码没落在该位该号"}.get(cls, cls)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", required=True, help="blogger_predictions_verify.json")
    ap.add_argument("--period", required=True)
    ap.add_argument("--draw", required=True)
    ap.add_argument("--out-dir", default=OUT_DIR)
    args = ap.parse_args()

    verify = json.load(open(args.verify, encoding="utf-8"))
    stats = verify.get("统计", {})
    images = verify.get("images", {})
    draw = [int(x) for x in args.draw.split()]
    ACTUAL = dict(zip(["万", "千", "百", "十", "个"], draw))
    DRAW = args.draw
    PERIOD = args.period

    # 命中 = 单押对位的记录（record.cls==hit）
    hit_recs = []
    nonhit_recs = []   # 剔分类 -> reason
    excluded_imgs = []
    for file, iv in sorted(images.items()):
        blogger = iv.get("blogger", "")
        if iv.get("status") == "excluded":
            excluded_imgs.append({"blogger": blogger, "file": file,
                                  "reason": iv.get("reason", "")})
            continue
        for r in iv.get("records", []):
            if r.get("cls") == "hit":
                hit_recs.append({"file": file, "blogger": blogger,
                                 "位置": r["位置"], "候选": r["候选"], "实际": r["实际"],
                                 "标注方式": r.get("标注方式", ""), "原文": r.get("原文", ""),
                                 "logic": iv.get("logic", ""),
                                 "predicted_positions": [
                                     {"位置": q["位置"], "候选": q["候选"],
                                      "标注方式": q.get("标注方式", ""), "原文": q.get("原文", "")}
                                     for q in iv.get("records", [])]})
            else:
                # 单码错位(miss) 属于「单码采集」的一部分（单押了只是没中），不剔除；
                # 只有 多码/和值(wide)、不定位(noloc) 才剔除出单码口径。
                if r.get("cls") in ("wide", "noloc"):
                    nonhit_recs.append({"blogger": blogger, "file": file,
                                        "cls": r["cls"], "位置": r["位置"],
                                        "候选": r["候选"], "x": r.get("实际")})

    total_collect = stats.get("单码采集", stats.get("预测位置采集", 0))
    n_hit = len(hit_recs)
    hit_rate = n_hit / total_collect if total_collect else 0

    # rules：每条命中 "1位置1中"
    rules = []
    for h in hit_recs:
        pos = norm_pos(h["位置"])
        pos_check = {pos: f"候选{h['候选']} 含实际{ACTUAL.get(pos)} ✓"}
        rules.append({
            "period": PERIOD, "draw": DRAW, "blogger": h["blogger"], "image": h["file"],
            "type": "定位", "hit_position": h["位置"], "hit_numbers": h["候选"],
            "multi": "1位置1中", "predicted_positions": h["predicted_positions"],
            "pos_check": pos_check,
            "logic": h.get("logic", "") or "博主在目标期行手写" + h["位置"] + "=" + ",".join(map(str, h["候选"]))
        })

    # rejected：按博主去重（保留一条），规则与 26230 一致
    rejected = []
    seen = set()
    for r in nonhit_recs:
        b = r["blogger"]
        if b not in seen:
            seen.add(b)
            rejected.append({"blogger": b,
                             "reason": f"{pos_why(r['cls'])}（{r['位置']} 候选{r['候选']} 实际{r['x']}）"})
    for e in excluded_imgs:
        if e["blogger"] and e["blogger"] not in seen:
            seen.add(e["blogger"])
            rejected.append({"blogger": e["blogger"], "reason": e["reason"]})

    n_full = sum(1 for r in rules if r["multi"].endswith("1中"))
    out = {"period": PERIOD, "draw": DRAW, "total_records": total_collect,
           "hit_records": n_hit, "hit_rate": round(hit_rate, 4), "full_hits": n_full,
           "rejected": rejected, "rules": rules, "rule_count": len(rules)}
    os.makedirs(args.out_dir, exist_ok=True)
    with open(os.path.join(args.out_dir, f"{PERIOD}.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    # Markdown
    L = [f"# 命中规律库 — {PERIOD} 期", ""]
    L.append(f"> 期号：**{PERIOD}** ｜ 开奖：**{DRAW}**（万 千 百 十 个） ｜ 口径：博主目标期行手写+单押")
    L.append(f"> 采集记录：**{total_collect} 条** ｜ 命中：**{n_hit} 条（{hit_rate:.2%}）** ｜ 完全命中（1位置1中）：{n_full} 条")
    L.append(f"> 规律条数：**{len(rules)} 条**")
    L.append("")
    L.append("| 博主 | 命中位置 | 全图预测明细（各位置对错） | 博主画规逻辑 |")
    L.append("|---|---|---|---|")
    for r in rules:
        details = []
        for p in r["predicted_positions"]:
            pp = norm_pos(p["位置"])
            cand = ",".join(map(str, p["候选"] or []))
            a = ACTUAL.get(pp, "?")
            ok = "✓" if (a is not None and a in (p["候选"] or [])) else "✗"
            details.append(f"{pp}{cand}{ok}(实际{a})")
        L.append(f"| {r['blogger']} | 定位 {r['hit_position']}={','.join(map(str,r['hit_numbers']))} → {PERIOD} {r['hit_position']}={ACTUAL.get(norm_pos(r['hit_position']))} ✓ | {'；'.join(details)} | {r['logic']} |")
    L.append("")
    L.append("## 被剔除的命中（本期修正）")
    L.append("")
    for r in rejected:
        L.append(f"- **{r['blogger']}**：{r['reason']}")
    L.append("")
    L.append("> 规律为博主手绘画规的历史总结，彩票开奖属独立随机事件，不具备预测效力。")
    with open(os.path.join(args.out_dir, f"{PERIOD}.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    print(f"规律库 → {args.out_dir}/")
    print(f"  采集 {total_collect} / 命中 {n_hit} ({hit_rate:.2%}) / 完全 {n_full} / 规律 {len(rules)} 条")
    print(f"  剔除 {len(rejected)} 条(按博主去重): {[r['blogger'] for r in rejected]}")


if __name__ == "__main__":
    main()
