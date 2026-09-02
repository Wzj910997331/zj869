#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cv_trend_reader/analyze_grid.py — 走势图网格自动探查。

输入: 博主原图(期×5位号码表)。
输出: 该图的行带(数据行 y 区间)、列中心(x)、底部期号 OCR、标注 blob 清单。
用于: 建立每张图的 (行→期号, 列→位) 映射, 支撑 CONFIRM 逐条验证。

本探查假设走势图为"期号列 + 5个数字列"表格式布局(多数博主图如此);
对非标准图(手写胆码、红底、无期号)输出 None, 由调用方特判。
"""
import sys
import os

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from reader import (load, color_masks, mask_stats, ocr_digits,  # noqa: E402
                    render_ascii)


def find_row_bands(img, dark_thresh=140, min_h=20, gap=10, min_rows=3):
    """水平投影找数字行带。返回 [(y0,y1), ...] 升序。无稳定结构→[]。

    数字行: 每行号码的深色像素形成水平带, 带间有较空的间隙。
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    _, bw = cv2.threshold(gray, dark_thresh, 255, cv2.THRESH_BINARY_INV)
    proj = bw.sum(axis=1) / 255.0
    # 只关注有数字的列区域(跳过左侧期号/右侧手写, 取中间 40% 宽度)
    x0, x1 = int(w * 0.2), int(w * 0.8)
    proj = bw[:, x0:x1].sum(axis=1) / 255.0
    proj[proj < max(3, (x1 - x0) * 0.05)] = 0
    # 分段
    segs = []
    inx = False
    s = 0
    for y in range(h):
        if proj[y] > 0:
            if not inx:
                s = y
                inx = True
        elif inx:
            segs.append([s, y - 1])
            inx = False
    if inx:
        segs.append([s, h - 1])
    # 合并相近带
    merged = []
    for a, b in segs:
        if merged and a - merged[-1][1] < gap:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    out = [(a, b) for a, b in merged if b - a + 1 >= min_h]
    if len(out) < min_rows:
        return []
    return out


def find_cols_in_band(img, y0, y1, min_w=18, gap=12):
    """某行带内垂直投影找列。返回 [(x0,x1), ...] 升序。"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    _, bw = cv2.threshold(gray, 140, 255, cv2.THRESH_BINARY_INV)
    strip = bw[y0:y1, :]
    proj = strip.sum(axis=0) / 255.0
    proj[proj < max(2, (y1 - y0) * 0.15)] = 0
    segs = []
    inx = False
    s = 0
    for x in range(w):
        if proj[x] > 0:
            if not inx:
                s = x
                inx = True
        elif inx:
            segs.append([s, x - 1])
            inx = False
    if inx:
        segs.append([s, w - 1])
    merged = []
    for a, b in segs:
        if merged and a - merged[-1][1] < gap:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged if b - a + 1 >= min_w]


def bottom_period(img, y0, y1, period_col=(0, 200)):
    """OCR 底部行带左侧期号。返回字符串(如 '6230')或 ''。"""
    roi = img[y0:y1, period_col[0]:period_col[1]]
    res = ocr_digits(roi, psm=7, upscale=5)
    return "".join(str(d) for d, _ in res)


def match_period(period_str, lottery):
    """期号串(可能缺前导 '26')匹配开奖历史。返回 period 或 None。"""
    if not period_str:
        return None
    for rec in lottery:
        p = str(rec["period"])
        # '6230' 匹配 '26230' 或 直接匹配
        if period_str == p or period_str == p[-len(period_str):]:
            return rec
    return None


def analyze_grid(img, lottery):
    """主探查。返回 dict, 结构不成立则返回 None。"""
    h, w = img.shape[:2]
    bands = find_row_bands(img)
    if not bands:
        return {"ok": False, "reason": "no_row_bands", "h": h, "w": w}
    # 底部行带
    y0, y1 = bands[-1]
    cols = find_cols_in_band(img, y0, y1)
    period = bottom_period(img, y0, y1)
    matched = match_period(period, lottery)
    return {
        "ok": True,
        "h": h, "w": w,
        "n_bands": len(bands),
        "bottom_band": (y0, y1),
        "bands_centers": [(a + b) // 2 for a, b in bands],
        "bottom_cols": cols,
        "bottom_period_ocr": period,
        "bottom_period_match": matched["period"] if matched else None,
        "prev_band": (bands[-2][0], bands[-2][1]) if len(bands) >= 2 else None,
    }
