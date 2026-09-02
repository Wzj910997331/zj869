#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_digit_dataset.py — 从已匹配期号行提取真实标注数字集 → 数据增强训练输入

数据来源: filter_trend 的 filter_report.json (period_pairs 里 matched=True 的行,
  tesseract 读到 4-6 位期号且与开奖历史匹配 = 期号真值已确认)。
两个标注通道:
  期号位(p): 底部期号列连通域游程(最左紧凑组) → 真值=匹配的期号 full;
  结果位(r): detect_columns 定位 5 结果列 + 开奖真值 → box 中心与列中心偏移 ≤12px。
清洗:
  列正则性门控: 5 结果列间距应接近相等(标准表格), 否则列定位不可信, 结果位整图作废;
  宽高比 0.35-1.5 / 暗像素 3%-65% / 尺寸 ≥150px。
每个 cell 存 (data, label, meta="<file>@<row>@<label>", offset, kind)。

用法:
  /usr/bin/python3 modules/image_recognize/build_digit_dataset.py --date 20260829 [--out npz]
  多日期: 对每个日期各跑一次, 再用 train_digits_real.py --npz <合并后的> 训练
         (train_digits_real.py 可直接接受 object 数组的 npz)
"""
import argparse
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import REPO, load_json  # noqa: E402
from crop_all import process_one  # noqa: E402
from filter_trend import detect_columns  # noqa: E402
from cv_trend_reader.reader import load, segment_digits_cc  # noqa: E402

THRESHOLDS = (210, 235, 220, 180, 120)
REG_GAP_TOL = 0.10        # 列间距相对中位数的容差(正则性门控)
OFF_MAX = 12.0            # box 中心 vs 列中心 偏移上限
ASPECT = (0.35, 1.5)      # 单数字宽高比
DARK = (0.03, 0.65)       # 暗像素占比
MIN_SIZE = 150


def seg_best(gray):
    best = []
    for thr in THRESHOLDS:
        _, bw = cv2.threshold(gray, thr, 255, cv2.THRESH_BINARY_INV)
        bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
        bx = segment_digits_cc(bw, gray.shape[0])
        if len(bx) > len(best):
            best = bx
    return best


def runs_of(boxes):
    """按间距聚类成游程(期号列=最左紧凑组)。返回 [run,...]。"""
    if not boxes:
        return []
    xs = sorted(boxes, key=lambda b: b[0])
    gaps = [xs[i + 1][0] - (xs[i][0] + xs[i][2]) for i in range(len(xs) - 1)]
    m = int(np.median(gaps)) if gaps else 0
    tol = max(int(1.8 * m), 34)
    runs, cur = [], [xs[0]]
    for i in range(1, len(xs)):
        gap = xs[i][0] - (xs[i - 1][0] + xs[i - 1][2])
        if gap > tol:
            runs.append(cur)
            cur = [xs[i]]
        else:
            cur.append(xs[i])
    runs.append(cur)
    return runs


def build(date):
    rep = load_json(os.path.join(REPO, "data", "crawl", date, "filter_report.json"))
    lottery = {r["period"]: r["numbers"]
               for r in load_json(os.path.join(REPO, "data", "crawl", date, "lottery_recent.json"))}
    img_dir = os.path.join(REPO, "data", "crawl", date, "images")

    cells = []
    s = {"img": 0, "matched_rows": 0, "cols_ok": 0, "period_cells": 0,
         "result_cells": 0, "off_drop": 0}
    for f, v in rep["images"].items():
        pairs = v.get("period_pairs") or []
        matched = [p for p in pairs if p.get("matched")]
        if not matched:
            continue
        s["img"] += 1
        s["matched_rows"] += len(matched)
        img = load(os.path.join(img_dir, f))
        h, w = img.shape[:2]
        try:
            status, pinfo, grid = process_one(os.path.join(img_dir, f))
        except Exception:
            continue
        rows = (grid or {}).get("rows") or []
        row_half = pinfo.get("row_half", 68)
        cols = []
        try:
            cols = detect_columns(img, pinfo, grid) if rows else []
        except Exception:
            cols = []
        if cols:
            gaps = [cols[i + 1] - cols[i] for i in range(len(cols) - 1)]
            med = float(np.median(gaps))
            regular = (med > 0 and max(g / med for g in gaps) < 1 + REG_GAP_TOL
                       and min(g / med for g in gaps) > 1 - REG_GAP_TOL)
            if not regular:
                cols = []            # 列不可信, 结果位作废, 只留期号位
            else:
                s["cols_ok"] += 1
        pitch = cols[1] - cols[0] if len(cols) > 1 else 80
        half = int(0.55 * pitch)
        x1 = max(200, int(w * 0.32))

        for p in matched:
            yc = int(p["row"])
            pf = str(p["period_full"])
            num = lottery.get(pf)
            y0, y1 = max(0, yc - row_half), min(h, yc + row_half)
            if y1 - y0 < 8:
                continue
            gray = cv2.cvtColor(img[y0:y1, :], cv2.COLOR_BGR2GRAY)

            # ---- 期号位(左带) ----
            g0 = gray[:, :x1]
            boxes = seg_best(g0)
            if boxes:
                run = (runs_of(boxes) or [[]])[0]
                n = len(pf)
                if len(run) >= n:
                    bu, la = run[:n], list(pf)
                elif len(run) >= 3:
                    bu, la = run, list(pf[-len(run):])
                else:
                    bu, la = [], []
                if bu:
                    for (bx, by, bw_, bh), lab in zip(bu, la):
                        pad = max(2, int(0.12 * bh))
                        cell = g0[max(0, by - pad):by + bh + pad,
                                  max(0, bx - pad):bx + bw_ + pad]
                        if cell.size:
                            cells.append((f, yc, int(lab), cell, -1.0, "p"))
                            s["period_cells"] += 1

            # ---- 结果位(5 列) ----
            if cols and num:
                for i, c in enumerate(cols):
                    xa, xb = max(0, c - half), min(gray.shape[1], c + half)
                    if xb <= xa or xb - xa < 10:
                        continue
                    crop = gray[:, xa:xb]
                    bs = seg_best(crop)
                    if not bs:
                        continue
                    bb = min(bs, key=lambda b: abs(b[0] + b[2] / 2 - (c - xa)))
                    off = abs(xa + bb[0] + bb[2] / 2 - c)
                    if off > OFF_MAX:
                        s["off_drop"] += 1
                        continue
                    bx, by, bw_, bh = bb
                    pad = max(2, int(0.12 * bh))
                    cell = crop[max(0, by - pad):by + bh + pad,
                                max(0, bx - pad):bx + bw_ + pad]
                    if cell.size:
                        cells.append((f, yc, int(num[i]), cell, off, "r"))
                        s["result_cells"] += 1

    # ---- 清洗 ----
    out = []
    for (f, yc, lab, cell, off, kind) in cells:
        h, w = cell.shape
        if w / h < ASPECT[0] or w / h > ASPECT[1]:
            continue
        if cell.size < MIN_SIZE:
            continue
        dark = (cell < 120).mean()
        if dark < DARK[0] or dark > DARK[1]:
            continue
        out.append((f, yc, lab, cell, off, kind))

    print(f"[build:{date}] 图 {s['img']} 匹配行 {s['matched_rows']} "
          f"cols_ok {s['cols_ok']} off_drop {s['off_drop']}")
    from collections import Counter
    print(f"[build:{date}] 原始 {len(cells)} → 清洗后 {len(out)}")
    print(f"[build:{date}] 类别: {dict(sorted(Counter(lab for _,_,lab,_,_,_ in out).items()))}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    cells = build(args.date)
    data = np.array([c[3] for c in cells], dtype=object)
    labels = np.array([c[2] for c in cells], dtype=np.int64)
    meta = np.array([f"{c[0]}@{c[1]}@{c[2]}" for c in cells], dtype=object)
    off = np.array([c[4] for c in cells], dtype=np.float32)
    kind = np.array([c[5] for c in cells], dtype=object)
    np.savez(args.out, data=data, labels=labels, meta=meta, offset=off, kind=kind)
    print(f"[build:{args.date}] 写 {args.out} ({len(cells)} cell)")


if __name__ == "__main__":
    main()
