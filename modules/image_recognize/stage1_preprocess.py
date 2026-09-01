#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stage 1：OpenCV 预处理 → grid_geometry.json（每图）

检测内容：
- 5 位置列中心（万348/千499/百648/十801/个949，垂直投影吸附模板，容差 ±22）
- 期号列 x 范围（x195-250）
- 行梳：pitch≈136 等距行槽（相位=行带中心模 pitch 中位数），逐槽标记填充
- 表头 y 范围（首个实心绿条）

算法依据（实测 6 图）：
- 绿色数字掩码 (G>150)&(R<210)&(B<150)，开运算，0/1 二值
- 表头 = 首个"行绿像素数 > 0.6*宽"的实心带
- 行带 = 绿像素>15 的连续行合并（高>=15px）
- 行梳 = 带中心模 pitch 求相位，扩展至数字区全部槽位
"""
import argparse
import os
import sys

import cv2
import numpy as np
from scipy.signal import find_peaks

from common import COLUMN_TEMPLATE, POS_NAMES, load_json, write_json, fix_print

GRID_YM = 430      # 数字区下界（表头之下）
GRID_YMAX = 2160   # 数字区上界
FILL_THRESH = 600  # 行槽 ±40 窗内绿像素数 > 此值视为填充


def green_mask(img):
    """0/1 二值绿色数字掩码。"""
    b, g, r = img[..., 0].astype(np.int16), img[..., 1].astype(np.int16), img[..., 2].astype(np.int16)
    mg = ((g > 150) & (r < 210) & (b < 150)).astype(np.uint8)
    mg = cv2.morphologyEx(mg, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    return mg


def detect_header(mg):
    """首个实心绿条（表头）：行绿像素数 > 0.6*宽。返回 (y0, y1)。"""
    xw = 800
    line = mg[:, 200:1000].sum(axis=1).astype(int)
    solid = line > 0.6 * xw
    bands = []
    inb = False
    for y, s in enumerate(solid):
        if s and not inb:
            y0 = y
            inb = True
        elif not s and inb:
            bands.append((y0, y - 1))
            inb = False
    if inb:
        bands.append((y0, len(solid) - 1))
    return bands[0] if bands else (256, 388)


def detect_columns(mg):
    """垂直投影（y GRID_YM..GRID_YMAX），峰吸附模板。"""
    vproj = mg[GRID_YM:GRID_YMAX, :].sum(axis=0).astype(float)
    peaks, _ = find_peaks(vproj, distance=40)
    cand = [p for p in peaks if 260 <= p <= 1000]
    cols = []
    used_template = []
    for t in COLUMN_TEMPLATE:
        near = [c for c in cand if abs(c - t) <= 22]
        if near:
            cols.append(int(min(near, key=lambda c: abs(c - t))))
            used_template.append(False)
        else:
            cols.append(t)
            used_template.append(True)
    return cols, used_template


def detect_rows(mg):
    """行带分段 → 模相位定行梳。返回 (pitch, rows, filled)。"""
    proj = mg[:, 320:1000].sum(axis=1).astype(int)
    # 行带分段
    active = proj > 15
    bands = []
    inb = False
    for y, a in enumerate(active):
        if a and not inb:
            y0 = y
            inb = True
        elif not a and inb:
            if y - y0 >= 15:
                bands.append((y0, y - 1))
            inb = False
    if inb and len(proj) - y0 >= 15:
        bands.append((y0, len(proj) - 1))
    if not bands:
        raise RuntimeError("未检出数字行带（绿字非行网格分布，非走势图版式）")
    centers = [(a + b) // 2 for a, b in bands]
    # pitch = 相邻中心差中位数（筛 100-170）
    diffs = [b - a for a, b in zip(centers, centers[1:]) if 100 <= b - a <= 170]
    pitch = int(np.median(diffs)) if len(diffs) >= 3 else 136
    # 模相位（残差接近 pitch 的映射到 0）
    residues = [c % pitch for c in centers]
    residues = [r if r <= pitch * 0.7 else r - pitch for r in residues]
    phase = int(np.median(residues)) % pitch
    # 扩展槽位：phase + k*pitch 落在 [GRID_YM, GRID_YMAX]
    rows = []
    k = (GRID_YM - phase + pitch - 1) // pitch
    while True:
        y = phase + k * pitch
        if y > GRID_YMAX:
            break
        rows.append(y)
        k += 1
    filled = [proj[max(0, int(y) - 40):int(y) + 40].sum() > FILL_THRESH for y in rows]
    return pitch, phase, rows, filled


def process_image(path):
    img = cv2.imread(path)
    if img is None:
        raise RuntimeError(f"读不到图片: {path}")
    mg = green_mask(img)
    header = detect_header(mg)
    cols, used_template = detect_columns(mg)
    pitch, phase, rows, filled = detect_rows(mg)
    proj = mg[:, 320:1000].sum(axis=1).astype(int)
    # 顶部部分行（表头与首行之间绿量 > 600 记一个标注行）
    partial_row = None
    if rows:
        first = rows[0]
        sub = mg[max(header[1] + 5, 0):max(0, int(first) - 60), 320:1000].sum(axis=1)
        if len(sub) and int(sub.sum()) > 600 and len(sub) > 2:
            partial_row = max(header[1] + 5, 0) + int(np.argmax(sub))
    return {
        "image_size": list(img.shape[:2]),
        "header_y": list(header),
        "column_centers": cols,
        "column_names": [POS_NAMES[i] for i in range(5)],
        "columns_template_used": used_template,
        "period_col": {"x0": 195, "x1": 250},
        "row_pitch": int(pitch),
        "row_phase": int(phase),
        "rows": [int(r) for r in rows],
        "rows_filled": [bool(b) for b in filled],
        "partial_row_y": partial_row,
        "n_rows": len(rows),
    }


def main():
    fix_print()
    ap = argparse.ArgumentParser(description="Stage 1: 网格几何检测")
    ap.add_argument("--manifest", required=True)
    args = ap.parse_args()

    manifest = load_json(args.manifest)
    if not manifest:
        print("[stage1] ERROR: 读不到 manifest", args.manifest)
        sys.exit(2)
    out_dir = manifest["out_dir"]
    results = {}
    for f in manifest["images"]:
        name = os.path.basename(f)
        geo = process_image(f)
        results[name] = geo
        expect = [348, 499, 648, 798, 949]
        dev = [abs(a - b) for a, b in zip(geo["column_centers"], expect)]
        n_fill = sum(geo["rows_filled"])
        flag = "OK" if max(dev) <= 25 and n_fill >= 10 else "CHECK"
        print(f"[stage1] {name}: cols={geo['column_centers']} "
              f"pitch={geo['row_pitch']} rows={len(geo['rows'])} "
              f"filled={n_fill}/{len(geo['rows'])} header={geo['header_y']}")
        print(f"[stage1]   列偏差={dev} -> {flag}")
    geo_path = os.path.join(out_dir, "grid_geometry.json")
    write_json(results, geo_path)
    print(f"[stage1] -> {geo_path}")


if __name__ == "__main__":
    main()
