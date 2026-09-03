#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hand_ocr.py — cols strip 博主手写单码的确定性字形提取（2026-09-03 起步）。

设计（基于 26231 真图形态调研：idx59 生活很无奈「蓝徽章」实为格子内巨大的手写数字，
idx5/idx62 等存在列锚不对博主自绘格 / 片段布局 → 提取失败应判「无法读」转 DS 兜底）：

1. 每张 cols strip：5 个开奖列中心已在 extract 阶段用上期打印行对拍钉死（meta.cols，
   条内 x = (c - x_range[0])*3）。
2. 每个位置 = 以列中心为中点的竖向窗口（半宽 = 相邻列距/2 - 边距）。
3. 窗口内取博主彩色墨迹（高饱和非白非暗）；去掉 <200px 噪点、贴窗左右边且贯穿高度
   的细描边线 → 剩下列出的连通域=博主在这一列写/画的候选。
4. 候选若恰一个且几何像单个字（宽高比/占格比在容限内）→ 紧致裁剪+白底二值字形
   （墨迹=黑，余=白）→ digit_cnn.predict(hand_cnn)。
5. 整条内任一「有墨但读不出/多个字形/不像字」→ 上层整条转 DS（本模块只吐 per-pos 结果，
   由上层判）。"""
import os
import numpy as np
import cv2

COL_POS = ["万", "千", "百", "十", "个"]

# 博主墨迹：高饱和、非纯白非纯黑。颜色覆盖 红/紫/粉/蓝/绿/橙。
def color_mask(a, sat_min=50, max_min=100, mean_lo=42, mean_hi=232):
    mx = a.max(2).astype(int)
    mn = a.min(2).astype(int)
    mean = a.sum(2) / 3.0
    return (mx - mn >= sat_min) & (mx >= max_min) & (mean >= mean_lo) & (mean <= mean_hi)


def _split_comp(mask):
    m = (mask.astype(np.uint8)) * 255
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    return n - 1, stats[1:], lab  # stats row: x,y,w,h,area


def clean_components(mask, speck=200):
    """去掉噪点与贴左右边的贯穿竖线（单元格描边/列分隔线），返回 [ (x,y,w,h,area), ...]。"""
    H, W = mask.shape
    n, stats, lab = _split_comp(mask)
    out = []
    for i in range(n):
        x, y, w, h, area = stats[i]
        if area < speck or w <= 2 or h <= 2:
            continue
        # 贴窗左/右边 + 细长贯穿高度 → 竖直描边线
        if w < max(6, 0.10 * W) and h > 0.6 * H and (x <= 3 or x + w >= W - 3):
            continue
        # 贴窗顶/底 + 细高横线（水平分隔线）
        if h < max(6, 0.08 * H) and w > 0.5 * W and (y <= 3 or y + h >= H - 3):
            continue
        out.append((int(x), int(y), int(w), int(h), int(area)))
    return out


def window_slice(tile, centers, i, gap):
    """第 i 个列窗的彩色二值图。返回 (crop_mask, x0)。"""
    W = tile.shape[1]
    hw = max(4, int(gap / 2) - 4)
    cx = centers[i]
    x0 = max(0, cx - hw)
    x1 = min(W, cx + hw)
    a = tile[:, x0:x1]
    return color_mask(a), x0


def glyph_ok(box, gap, H):
    """几何容限：一个字应大致在格内中部、宽≤列距、高≤条高，长宽比不像描边/整格填充。"""
    x, y, w, h, area = box
    if w > gap * 1.15 or h > H * 1.02:
        return False
    if h < 0.10 * H:            # 太矮（横线/下划线）
        return False
    asp = w / max(1.0, h)
    if asp > 1.9 or asp < 0.22:  # 手写数字大致方形到窄高
        return False
    return True


def extract_pos_glyphs(tile, meta, boxes=None, gap=None):
    """对整条 cols strip 提取 5 位置字形候选。

    meta: manifest 条目（strip_type=cols），需含 x_range/cols/col_positions。
    返回 dict: {pos: {'cx':int,'ok':bool,'reason':str,'box':[x,y,w,h],'mask':crop_mask(uint8)
                      'conf'..由上层填}}
    """
    x0m = meta["x_range"][0]
    centers = [(int(c) - x0m) * 3 for c in meta["cols"]]
    poses = meta.get("col_positions") or list(COL_POS)
    posd = {p: c for p, c in zip(poses, centers)} if len(poses) == len(centers) else \
        {COL_POS[i]: centers[i] for i in range(min(5, len(centers)))}
    gaps = [centers[i + 1] - centers[i] for i in range(len(centers) - 1)]
    gap = min(gaps) if gaps else int(tile.shape[1] * 0.18)
    H = tile.shape[1] if False else tile.shape[0]
    res = {}
    for p in COL_POS:
        if p not in posd:
            res[p] = {"ok": False, "reason": "no-col-anchor", "cx": None}
            continue
        cx = posd[p]
        W = tile.shape[1]
        hw = max(4, int(gap / 2) - 4)
        x0 = max(0, cx - hw)
        x1 = min(W, cx + hw)
        cm = color_mask(tile[:, x0:x1])
        comps = clean_components(cm)
        comps = [b for b in comps if glyph_ok(b, gap, H)]
        if not comps:
            res[p] = {"ok": False, "reason": "no-glyph", "cx": cx, "x0": x0}
            continue
        # 多个分离字形 → 无法定夺（同格多字/并列）
        # 只取面积最大者，但若次大者面积>40%且分离较远 → 视为 multi
        comps.sort(key=lambda b: -b[4])
        big = comps[0]
        multi = False
        if len(comps) > 1 and comps[1][4] > 0.35 * big[4]:
            multi = True
        res[p] = {"ok": not multi, "reason": "multi-glyph" if multi else "single",
                  "cx": cx, "x0": x0, "box": big, "multi": multi}
    return res


def glyph_stroke_crop(tile, x0, box, margin=6):
    """字形→(黑白二值灰度 uint8, tight bbox)。墨迹=0(黑)，余=255(白)，pad margin。"""
    x, y, w, h, _ = box
    H, W = tile.shape[:2]
    xa = max(0, x0 + x - margin)
    xb = min(W, x0 + x + w + margin)
    ya = max(0, y - margin)
    yb = min(H, y + h + margin)
    win_a = tile[ya:yb, xa:xb]
    m = color_mask(win_a)
    g = np.where(m, 0, 255).astype(np.uint8)
    return g


def classify_glyph(glyph_gray, model=None, path=None, predict=None):
    """返回 (digit, conf, probs, top2_gap) 或 None。path 指向 hand_cnn.pt。"""
    from digit_cnn import load_model, predict as _p, uncertain_decision
    if model is None:
        model = load_model(device="cpu", path=path)
    if model is None:
        return None
    r = _p(glyph_gray, model=model)
    if r is None:
        return None
    digit, conf, probs = r
    order = np.argsort(probs)[::-1]
    gap = float(probs[order[0]] - probs[order[1]])
    return int(digit), float(conf), probs, gap
