#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""calib_anchor.py — 上一期开奖行对拍：读某一行 5 个开奖数字，对拍权威开奖。

v4 定位：切窄条是「横着切整块」，不需要列对齐。这里的「猜列位置」只是**读数字的手段**
（逐列读需要知道大概在哪读），不参与切窄条的输出对齐。读出来的匹配位数只用于
「多期容错验证纵向定位对不对」（filter_trend 第 3 步）。

复用改动 A（extract_prediction_strip.cnn_anchor_cols）的 3 候选集 + 逐列读逻辑，
但目的从「钉死 5 列 x」降级为「读对 5 数字、返回匹配位数」。
"""
import cv2
import numpy as np


def read_col_digit(gray, cx, half, predict, model):
    """在已知列中心 cx 处 CNN 读单个打印数字。窗口 [cx-half, cx+half] 直接送 digit_cnn
    （内部会 resize 到 32×48）。返回 int digit 或 None。

    打印数字比博主手写规整，窄条带里在列中心附近几乎就是该位数字，逐列读远比全带连通域
    切分稳（后者会把左侧期号列"26231"当 5 位主串、漏掉右侧打印数字）。
    """
    x0 = max(0, int(cx) - half)
    x1 = min(gray.shape[1], int(cx) + half)
    if x1 - x0 < 8:
        return None
    r = predict(gray[:, x0:x1], model=model)
    return int(r[0]) if r else None


def match_row_draw(band_gray, base_cols, draw, predict, model):
    """读某一行的 5 个开奖数字，对拍权威开奖 draw，返回最佳匹配位数 0–5。

    以 base_cols（投影列位，全图 x 坐标）为候选起点，生成 3 个候选集（本体 / 丢最左右补
    1 列 / 左补 1 列丢最右），逐列 read_col_digit 对齐，取 (匹配数高, 间距最均匀) 的最优。
    3 候选集覆盖期号/和值列混入导致的整串偏移 1 格；间距均匀性破「重叠 4 位」平局。

    band_gray 必须是**全宽**灰度图（行带 img[row-half:row+half] 的灰度），这样 base_cols
    的全图 x 坐标直接对齐。draw 不足 5 位 / base_cols 不足 5 列 / pitch 无意义 → 返回 0。
    """
    if not draw or len(draw) != 5:
        return 0
    base_cols = sorted(int(c) for c in (base_cols or []) if c)
    if len(base_cols) < 5:
        return 0
    gaps = [base_cols[i + 1] - base_cols[i] for i in range(len(base_cols) - 1)]
    pitch = int(np.median(gaps)) if gaps else 0
    if pitch <= 1:
        return 0
    hw = max(20, pitch // 2)

    def gap_cost(cs):
        g = [cs[i + 1] - cs[i] for i in range(4)]
        return float(np.std(g)) if g else float("inf")

    def score(cs):
        ds = [read_col_digit(band_gray, c, hw, predict, model) for c in cs]
        m = sum(1 for d, a in zip(ds, draw) if d == a)
        return m, -gap_cost(cs)

    variants = [base_cols]
    variants.append(base_cols[1:] + [base_cols[-1] + pitch])   # 期号在左：丢最左，右补 1 列
    variants.append([base_cols[0] - pitch] + base_cols[:-1])   # 期号在右：左补 1 列，丢最右
    best = None  # ((matches, -gap_cost), centers)
    for cs in variants:
        if any(c < 0 or c >= band_gray.shape[1] for c in cs):
            continue
        m, gc = score(cs)
        if best is None or (m, gc) > best[0]:
            best = ((m, gc), cs)
    return best[0][0] if best else 0
