#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cv_trend_reader/reader.py — OpenCV 走势图解析核心(确定性, 无视觉模型)。

目标: 把博主手绘标注的走势图(期×5位号码表)解析为结构化标注文本,
      供下游"博主预测 → 命中核验"使用, 替代慢速视觉大模型。

本模块不做任何 LLM 调用, 全 OpenCV + tesseract + ASCII 目视复核。

设计原则: 走势图形态差异极大(有/无网格线、有/无红标签、全宽色带/圈选/框选/
          手写), 因此任何单一"通用网格检测"都不可靠。本模块提供一组可组合的
          原语(find_rows / find_columns / detect_annotations / bottom_align /
          render_ascii / ocr_cell), 由调用方按图自适应编排, 并在验证脚本里
          用开奖历史做强约束交叉验证。
"""
import io
import os
import subprocess
import sys
import tempfile
import threading

import cv2
import numpy as np

# ---------------------------------------------------------------- 基础 IO

def load(path):
    """加载图片(支持中文路径/任意扩展名)。"""
    data = np.fromfile(path, np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"无法解码图片: {path}")
    return img


def save(img, path):
    """保存图片(支持中文路径)。"""
    ext = os.path.splitext(path)[1].lower() or ".png"
    ok, buf = cv2.imencode(ext, img)
    if not ok:
        raise ValueError(f"编码失败: {path}")
    buf.tofile(path)


# ---------------------------------------------------------------- 颜色掩码

COLOR_DEFS = {
    "red":    ([0, 0, 150], [90, 90, 255]),
    "red2":   ([0, 0, 120], [100, 100, 255]),   # 更宽松的红
    "blue":   ([150, 0, 0], [255, 110, 110]),
    "green":  ([0, 150, 0], [110, 255, 110]),
    "yellow": ([0, 150, 150], [110, 255, 255]),
    "orange": ([0, 120, 200], [120, 200, 255]),  # 橙色底框(叼123)
    "purple": ([120, 0, 120], [255, 110, 255]),
    "pink":   ([150, 100, 200], [255, 180, 255]),
}


def color_masks(img, colors=None):
    """返回 {colorname: mask}。colors=None 表示全部定义色。"""
    out = {}
    for name, (lo, hi) in COLOR_DEFS.items():
        if colors and name not in colors:
            continue
        out[name] = cv2.inRange(img, np.array(lo), np.array(hi))
    return out


def mask_stats(mask):
    """连通域统计: 返回 [(x,y,w,h,area), ...] 按面积降序。"""
    n, lab, stats, cent = cv2.connectedComponentsWithStats(mask, 8)
    return [(int(stats[i][0]), int(stats[i][1]), int(stats[i][2]),
             int(stats[i][3]), int(stats[i][4]))
            for i in range(1, n)]


# ---------------------------------------------------------------- 行列定位

def find_columns_proj(img, min_w=20, gap=18):
    """垂直投影找数字列(深色像素密度峰)。

    走势图每格一个数字, 数字列方向形成规则密度峰。返回 [(x0,x1), ...]。
    对"整图连成一片"的图(投影无峰)返回 []。
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    _, bw = cv2.threshold(gray, 140, 255, cv2.THRESH_BINARY_INV)
    proj = bw.sum(axis=0) / 255.0
    proj[proj < max(3, h * 0.02)] = 0
    # 分段
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
    # 合并相近段
    merged = []
    for a, b in segs:
        if merged and a - merged[-1][1] < gap:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged if b - a + 1 >= min_w]


def find_rows_red_labels(img, w_pad=0.35, h_min=25, h_max=140):
    """用左侧红色行标签锚切行。

    每行左侧有一个红色小方块/数字标签。返回行中心 y 列表(升序)。
    无红标签的图返回 []。
    """
    rmask = cv2.inRange(img, np.array([0, 0, 150]), np.array([90, 90, 255]))
    h, w = img.shape[:2]
    # 只取左侧 35% 区域
    region = rmask[:, : int(w * w_pad)]
    blobs = mask_stats(region)
    ys = []
    for x, y, bw_, bh, area in blobs:
        if area > 400 and h_min <= bh <= h_max and bw_ >= 15:
            ys.append(y + bh // 2)
    ys.sort()
    # 合并相近行
    out = []
    for y in ys:
        if out and y - out[-1] < 8:
            continue
        out.append(y)
    return out


def cluster_rows_from_red(img):
    """红标签锚 + 全宽色带联合: 返回行边界候选。"""
    rmask = cv2.inRange(img, np.array([0, 0, 150]), np.array([90, 90, 255]))
    h, w = img.shape[:2]
    blobs = mask_stats(rmask)
    y_edges = []
    for x, y, bw_, bh, area in blobs:
        # 全宽色带(>0.6w) → 上下边是行边界
        if bw_ > 0.6 * w and bh > 60:
            y_edges.extend([y, y + bh])
        # 中等宽度矩形(框选一行数字, ~0.3-0.6w) → 也是行标注
        elif 0.25 * w < bw_ < 0.6 * w and bh > 60 and area > 5000:
            y_edges.extend([y, y + bh])
    y_edges = [y for y in y_edges if 0 < y < h]
    y_edges = sorted(set(y_edges))
    return y_edges


# ---------------------------------------------------------------- 标注检测

def detect_annotations(img, min_area=400, colors=None):
    """检测彩色标注 blob。

    返回 dict {color: [(x,y,w,h,area,kind), ...]} kind ∈ {band,box,ring,dot}
      band  = 近全宽色带(整行高亮)
      box   = 中宽矩形(框选)
      ring  = 小且近乎方形(圈选/手写)
      dot   = 极小
    """
    masks = color_masks(img, colors)
    h, w = img.shape[:2]
    out = {}
    for name, mask in masks.items():
        items = []
        for x, y, bw_, bh, area in mask_stats(mask):
            if area < min_area:
                continue
            if bw_ > 0.8 * w and bh > 60:
                kind = "band"
            elif 0.2 * w <= bw_ <= 0.8 * w and bh > 40 and area > 3000:
                kind = "box"
            elif bw_ > 15 and bh > 15 and max(bw_, bh) < 260:
                kind = "ring"
            else:
                kind = "dot"
            items.append((x, y, bw_, bh, area, kind))
        items.sort(key=lambda t: -t[4])
        if items:
            out[name] = items
    return out


# ---------------------------------------------------------------- OCR

_CNN = None  # 懒加载缓存: (model, predict) 或 False(加载失败)
_CNN_LOCK = threading.Lock()


def _cnn_backend():
    """懒加载 digit_cnn 模型。返回 (model, predict) 或 None。

    小模型多线程池开销远大于计算(64 线程池每前向 ~360ms), 固定 1 线程。
    16 worker 线程池并发首调时需加锁避免重复 import torch。
    """
    global _CNN
    if _CNN is None:
        with _CNN_LOCK:
            if _CNN is None:
                try:
                    import torch
                    torch.set_num_threads(1)
                    model_dir = os.path.join(
                        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "model")
                    if model_dir not in sys.path:
                        sys.path.insert(0, model_dir)
                    from digit_cnn import load_model, predict
                    m = load_model(device="cpu")
                    _CNN = (m, predict) if m is not None else False
                except Exception:
                    _CNN = False
    return _CNN or None


def segment_digits_cc(bw, roi_h):
    """连通域切分数字。过滤小噪点 + 触顶/触底竖线(边框/网格线) + 粘连宽组件投影谷切开。

    返回 [(x,y,w,h), ...] 按 x 升序。数字在行带中居中悬浮, 竖线贯穿全高 → 排除。
    """
    n, _, stats, _ = cv2.connectedComponentsWithStats(bw, 8)
    comps = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < 40 or w < 4:
            continue
        if h < max(10, int(roi_h * 0.30)) or h > int(roi_h * 0.85):
            continue
        if y <= 1 or y + h >= roi_h - 1:  # 触顶/触底=竖线/边框, 非数字
            continue
        comps.append((x, y, w, h))
    comps.sort(key=lambda c: c[0])
    boxes = []
    for x, y, w, h in comps:
        if w <= 1.5 * h:
            boxes.append((x, y, w, h))
            continue
        # 粘连多字: 在投影谷处二分(暗像素尽量均衡分到两半)
        sub = bw[y:y + h, x:x + w]
        vp = sub.sum(axis=0)
        stack = [(0, w)]
        while stack:
            a, b = stack.pop()
            if b - a <= 2 or (b - a) <= 1.5 * h:
                boxes.append((x + a, y, b - a, h))
                continue
            best_i, best_ratio = -1, 1e9
            for i in range(a + 1, b - 1):
                left, right = vp[a:i].sum(), vp[i:b].sum()
                if left == 0 or right == 0:
                    continue
                ratio = max(left, right) / min(left, right)
                if ratio < best_ratio:
                    best_ratio, best_i = ratio, i
            if best_i < 0:
                boxes.append((x + a, y, b - a, h))
            else:
                stack.append((a, best_i))
                stack.append((best_i, b))
    boxes.sort(key=lambda b: b[0])
    return boxes


def _digit_band(bw, y_pad=6, min_h=8, frac_lo=0.02, frac_hi=0.85):
    """二值图 → 数字行带 (y0, y1)。行投影找"适量暗像素"行(2%-85%)的最长连续段。

    背景行两种极端都被排除: 白底 → frac≈0; 深底(整条被阈值吞成前景) → frac≈1。
    数字行暗密度落在中间 → 被选中, 从而把条带裁剪到数字真正所在的行带,
    让 segment_digits_cc 的高度比例过滤(0.30-0.85)对瘦小的期号数字有意义。
    """
    h, w = bw.shape
    if h < 8 or w < 4:
        return None
    frac = bw.sum(axis=1) / 255.0 / w
    digit_rows = (frac >= frac_lo) & (frac <= frac_hi)
    best_s, best_l, cs, cl = -1, 0, -1, 0
    for i, dr in enumerate(digit_rows):
        if dr:
            if cs < 0:
                cs = i
            cl += 1
        else:
            if cl > best_l:
                best_s, best_l = cs, cl
            cs, cl = -1, 0
    if cl > best_l:
        best_s, best_l = cs, cl
    if best_s < 0 or best_l < min_h:
        return None
    return max(0, best_s - y_pad), min(h, best_s + best_l + y_pad)


def _gap_split_period(boxes, max_ratio=2.5, min_gap=30):
    """按相邻 box 大间隙把"期号 5 位 + 结果列首位"分开, 取左侧期号组。

    期号 5 位间距小(~15-25px), 期号与右侧结果列之间隔一大段空白(~100px+);
    最大间隙 > max_ratio × 中位间隙 即在此切开, 取左组。切不出就原样返回。
    """
    if len(boxes) < 2:
        return boxes
    gaps = [boxes[i + 1][0] - (boxes[i][0] + boxes[i][2]) for i in range(len(boxes) - 1)]
    med = np.median(gaps)
    if med <= 0:
        return boxes
    i = int(np.argmax(gaps))
    if gaps[i] > max_ratio * med and gaps[i] > min_gap:
        return boxes[:i + 1]
    return boxes


def ocr_digits_cnn(img_roi, thresholds=(235, 210, 220, 180, 150, 120, 90, 60),
                   bright_thresholds=(150, 190, 220)):
    """CNN 读 ROI 中的数字串: 多档阈值连通域切分 → digit_cnn 逐字。

    返回 [(digit, conf), ...] conf=0~1; 无模型/无切分 → []。

    鲁棒性(v2, 2026-09-02 期号条带实测):
      ① 数字行带裁剪: 投影找数字所在行带, 解决"条带很高、数字只占 1/4"导致
         高度过滤(0.30-0.85×roi_h)把瘦小时期数字全过滤掉的失败。
      ② 低阈值扩展(90/60): 深底图(BGR 均值 89 这类)浅色阈值把整个背景吞成
         前景单 blob → 切分失败; 低阈值只抓最黑的前景数字。
      ③ 亮字方向: 白字/浅字在深底(前景=亮像素)时 BINARY_INV 全失败, 补正向
         THRESH_BINARY 档。
      ④ 期号列间隙切分: 条带常带上结果列首位, 按大间隙把期号 5 位切出来,
         避免返回 "26233X" 这种 6 位串导致期号不匹配。
    """
    back = _cnn_backend()
    if back is None:
        return []
    model, predict = back
    gray = cv2.cvtColor(img_roi, cv2.COLOR_BGR2GRAY)
    if gray.size == 0:
        return []
    candidates = []

    def _run(bw):
        band = _digit_band(bw)
        if band is None:
            return
        y0, y1 = band
        band_bw = bw[y0:y1]
        if band_bw.size == 0 or y1 - y0 < 8:
            return
        boxes = segment_digits_cc(band_bw, y1 - y0)
        boxes = _gap_split_period(boxes)
        if len(boxes) < 3:
            return
        out = []
        for x, y, w, h in boxes:
            pad = max(2, int(0.12 * h))
            cell = img_roi[y0 + max(0, y - pad):y0 + y + h + pad,
                           max(0, x - pad):x + w + pad]
            if cell.size == 0:
                continue
            r = predict(cell, model=model)
            if r:
                out.append((int(r[0]), float(r[1])))
        if out:
            candidates.append(out)

    for thr in thresholds:
        _, bw = cv2.threshold(gray, thr, 255, cv2.THRESH_BINARY_INV)
        bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
        _run(bw)
    for thr in bright_thresholds:
        _, bw = cv2.threshold(gray, thr, 255, cv2.THRESH_BINARY)
        bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
        _run(bw)

    if not candidates:
        return []
    c46 = [o for o in candidates if 4 <= len(o) <= 6]
    pool = c46 or candidates
    return max(pool, key=lambda o: (len(o), sum(c for _, c in o)))


def ocr_digits(img_roi, psm=7, whitelist="0123456789", upscale=4,
               threshold=120):
    """读 ROI 中的数字。OCR 引擎由环境变量 OCR_ENGINE 控制:
      auto(默认) = CNN 可用则 CNN, 否则 tesseract; cnn / tesseract 强制指定。

    CNN 返回 [(digit, conf)], conf=0~1; tesseract 返回 conf=100。
    调用方(期号校验/配对)只取数字串, 两种引擎输出格式一致。
    """
    engine = os.environ.get("OCR_ENGINE", "auto")
    if engine in ("cnn", "auto"):
        res = ocr_digits_cnn(img_roi)
        if res:
            return res
        if engine == "cnn":
            return []
    # tesseract 路径(原实现): 每 ROI 一次子进程
    gray = cv2.cvtColor(img_roi, cv2.COLOR_BGR2GRAY)
    if gray.size == 0:
        return []
    _, bw = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY_INV)
    bw = cv2.resize(bw, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC)
    ok, enc = cv2.imencode(".png", bw)
    if not ok:
        return []
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
        tf.write(enc.tobytes())
        tmp = tf.name
    try:
        out = subprocess.run(
            ["tesseract", tmp, "-", "--psm", str(psm), "-c",
             f"tessedit_char_whitelist={whitelist}"],
            capture_output=True, text=True, timeout=60)
        text = out.stdout.strip()
    finally:
        os.unlink(tmp)
    res = []
    for ch in text:
        if ch.isdigit():
            res.append((int(ch), 100))
    return res


# ---------------------------------------------------------------- ASCII 目视

_CHARS = "#%&MW*+=-:,. "  # 亮→空, 暗→字符


def render_ascii(img, bbox=None, W=100, color_marks=None, threshold=150):
    """把 ROI 渲染成 ASCII 字符画, 用于无视觉模型"目视"图结构。

    bbox=(x,y,w,h) 默认全图。W=输出字符宽(高按比例)。
    color_marks: 若给 {colorname:(lo,hi)}, 命中颜色像素映射为对应字母(如 R/B/O),
                 否则纯亮度。返回多行字符串。
    """
    if bbox is None:
        x, y, w, h = 0, 0, img.shape[1], img.shape[0]
    else:
        x, y, w, h = bbox
    roi = img[y:y + h, x:x + w]
    if roi.size == 0:
        return "<empty>"
    gh = roi.shape[0]
    W = max(16, min(W, 220))
    H = max(8, int(W * gh / roi.shape[1] * 0.45))
    # 亮度字符
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (W, H), interpolation=cv2.INTER_AREA)
    lines = []
    # 颜色标记掩码(缩小)
    cmark = {}
    if color_marks:
        for name, (lo, hi) in color_marks.items():
            m = cv2.inRange(roi, np.array(lo), np.array(hi))
            m = cv2.resize(m, (W, H), interpolation=cv2.INTER_AREA)
            cmark[name] = m > 128
    for yy in range(H):
        line = []
        for xx in range(W):
            ch = _CHARS[min(len(_CHARS) - 1, int(small[yy, xx] / 256 * len(_CHARS)))]
            if cmark:
                for name, m in cmark.items():
                    if m[yy, xx]:
                        ch = name[0].upper()
                        break
            line.append(ch)
        lines.append("".join(line))
    return "\n".join(lines)


# ---------------------------------------------------------------- 工具

def cell_box(rows, cols, r, c):
    """由行列边界数组求格子 (x,y,w,h)。rows/cols 为边界值列表(升序)。"""
    x0 = cols[c]
    x1 = cols[c + 1] if c + 1 < len(cols) else cols[c] + 40
    y0 = rows[r]
    y1 = rows[r + 1] if r + 1 < len(rows) else rows[r] + 40
    return (x0, y0, x1 - x0, y1 - y0)


def nearest_idx(vals, v):
    """在升序数组 vals 中找 v 应插入的位置索引(前一个索引)。"""
    import bisect
    i = bisect.bisect_right(vals, v) - 1
    return max(0, i)
