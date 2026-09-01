#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""spotcheck_reread_26233.py — 独立 ds 重读抽查。

对选定的 ds-ok 图,用与主流水线完全相同的 read prompt + ds 模型,但**不带任何
已存读数**,让模型全新读一遍 02_annotated.png。若独立读出的行序/期号/开奖号
与存档逐行一致 → 图片像素→read 链路成立（ds 幻觉无法在两次独立调用里复现
完全相同的一串 5 位开奖号）。

用法:
  /usr/bin/python3 modules/image_recognize/spotcheck_reread_26233.py \
      --date 20260831 --target-period 26233 --target-draw "1 6 3 4 0" \
      [--file s_2_25ee6a97-4190-4a56-a989-de77fccb5108_0.jpg ...]
"""
import argparse
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "modules", "image_recognize"))
import analyze_crops_ds as ac  # noqa: E402
from common import load_json  # noqa: E402

DS_MODEL = ac.DS_MODEL


def load_context(date):
    an = load_json(os.path.join(REPO, "data", "recognize", f"{date}_all", "analysis",
                                f"analyze_{date}.json"))
    man = load_json(os.path.join(REPO, "data", "recognize", f"{date}_all",
                                 "crops_all_manifest.json"))
    lot = load_json(os.path.join(REPO, "data", "crawl", date, "lottery_recent.json"))
    return an["images"], man["images"], lot


def compare_row(fresh, stored):
    """返回 (agree, detail)：read 数字/期号逐位对比。"""
    if stored.get("read") is None and fresh.get("read") is None:
        return True, "均空"
    if stored.get("read") is None or fresh.get("read") is None:
        return False, f"读否不一致(存档={stored.get('read')} 新读={fresh.get('read')})"
    r, s = fresh["read"], stored["read"]
    if len(r) != len(s):
        return False, f"长度不同 新{r} vs 存{s}"
    diffs = [i for i in range(len(r)) if r[i] != s[i]]
    if diffs:
        return False, f"位{diffs} 不同 新{r} vs 存{s}"
    # 数字一致再比期号
    fp, sp = fresh.get("period"), stored.get("period")
    if fp and sp and fp != sp:
        return False, f"数字同但期号不同 新{fp} vs 存{sp}"
    return True, f"全同 {r}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="20260831")
    ap.add_argument("--target-period", default="26233")
    ap.add_argument("--target-draw", default="1 6 3 4 0")
    ap.add_argument("--file", action="append", default=[])
    ap.add_argument("--timeout", type=int, default=45)
    ap.add_argument("--max-tokens", type=int, default=16000)
    args = ap.parse_args()
    target_draw = [int(x) for x in args.target_draw.split()]

    imgs, crops, lottery = load_context(args.date)
    if not args.file:
        args.file = ["s_2_25ee6a97-4190-4a56-a989-de77fccb5108_0.jpg",
                     "s_2_38c0ffda-3964-42d1-8d86-774598bb208e_0.jpg",
                     "s_2_4ad90bbd-f1eb-4d53-98ed-40019ad061fc_1.jpg"]

    out_root = os.path.join(REPO, "data", "recognize", f"{args.date}_all")
    total_agree = total_rows = 0
    print("=" * 74)
    for f in args.file:
        rec = imgs.get(f)
        if rec is None:
            print("跳过(无存档):", f)
            continue
        crop = crops.get(f, {})
        img = os.path.join(out_root, crop.get("crop_dir", ""), "02_annotated.png")
        n_rows = max(crop.get("annotated_rows") or [0]) + 1
        r = ac.run_vision_pass(img, n_rows, crop, DS_MODEL, args.target_period,
                               target_draw, lottery, args.timeout, args.max_tokens,
                               is_glm=False)
        print(f"\n文件: {f}  | 博主: {rec.get('blogger')}  | 用时 {r['seconds']}s")
        if not r["ok"]:
            print("  ✗ 新读失败:", r.get("error"))
            continue
        fresh = r["mapping"]
        stored = rec.get("rows") or {}
        # key 归一化为 int（JSON 键是 str，normalize_rows 可能返回 int）
        fresh = {int(k): v for k, v in fresh.items()}
        stored = {int(k): v for k, v in stored.items()}
        val = r["val"]
        print(f"  新读校验: {val['hard_failures'] or '全过'} "
              f"(matched {val['metrics']['n_matched']}, 方向={val['direction']}, "
              f"最新期={val['metrics']['max_period']})")
        agree = disagree = 0
        all_rows = sorted(set(list(fresh.keys()) + list(stored.keys())))
        for k in all_rows:
            fr = fresh.get(k, {})
            st = stored.get(k, {})
            ok, detail = compare_row(fr, st)
            if ok:
                agree += 1
            else:
                disagree += 1
                mark = "✗" if ok is False else "?"
                print(f"    row{k}: {mark} {detail}")
        total_agree += agree
        total_rows += agree + disagree
        verdict = "✓ 与存档一致" if disagree == 0 else f"⚠ {disagree} 行不一致"
        print(f"  行对比: {agree}/{agree + disagree} 一致  {verdict}")
    print("=" * 74)
    print(f"抽查合计: {total_agree}/{total_rows} 行一致")


if __name__ == "__main__":
    main()
