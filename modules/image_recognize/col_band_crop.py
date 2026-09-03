#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""col_band_crop.py — 「上一期+目标期」两整格带裁剪 + 目标格逐位博主标记自适应窗口（2026-09-03）。

为什么要有这个模块（踩坑史，完整思路见根 README §0b-①b）：
  旧法 band_top = ty − 1.5·row_pitch，隐含假设 filter 报的 target_y(ty) 是目标格中心。
  但部分图 ty 偏到自己格子的底部（idx56：ty=1641，真实目标格=[1560,1664] 中心≈1612，偏 +30px），
  ty−1.5p=1486 直接切进上一期 26230 的格子内部 → 上期开奖行 9 4 6 8 3 顶部被裁。
  教训：**ty 不可信为格中心，横向格线才是竖向唯一可信几何**（格线定格、格内装打印行）。

设计（确定性，零视觉）：
  1. detect_gridlines：多阈值(195..248)找**细横向格线**（行内最长连续暗 run≥0.85×表宽 且带厚≤6px）。
     阈值拉宽是为同时覆盖 idx59(格线灰<200) 与 idx56(格线偏浅需~215)；细带判据剔除满高彩色
     色带/内容厚带，跑通率远高于固定阈。
  2. resolve_band_rows：
       g       = 比 ty 小的最大格线 = **目标格顶** = 上期/目标两行的分界线；
       band_top = 最接近 g−pitch 的格线，但要求 < g−0.6·pitch（跳过孪生双线：idx62 的
                  1704/1710 靠 6px、idx22 的 1503/1555 中继线），即**上一期格的顶线**；
       cell_bot = 最接近 g+pitch 的格线 = 目标格底线。
       两整格 = [band_top, cell_bot]；缺线时逐级回退 ty−1.5p / ty−0.5p（method='pitch-fallback'）。
  3. 博主标记只认**起点在目标格内**的色块：扫描区 [g, cell_bot+0.9p] 但丢弃 起点已越过格底
     （= 下邻打印行的彩色开奖字，会被误当博主标记）；整区高的水印/色带（触区顶与底）剔除。
     条底 band_bot = max(ty+0.5p, cell_bot, 标记底+14) —— 博主色带不撑条、真标记才撑。
  4. 逐位自适应窗口：列锚好的图按 5 列中线切列窗，窗内挑垂直中心最贴目标格中的色块，
     窗口 = 色块 bbox 放大 25% 边距，再按目标高 ~120px 放大 → 预测值大数字(可近整格高)不裁缺。

产物（out/）：
  <stem>_band.png        3× 干净双行带（无参考线，供下游读/送 DS）
  <stem>_band_anno.png   --anno 时输出：同带 + 绿线=带顶(上期格顶) / 红线=上期-目标分界，人眼复核
  <stem>_<位置>.png      目标格内逐位博主标记自适应窗口（列可锚时）
  crop_report.json       每图 band 几何 + 列可锚性 + 逐位标记 bbox + 条内内容自检

用法：
  python3 modules/image_recognize/col_band_crop.py \
      --date 20260829 \
      --images data/crawl/20260829/images \
      --manifest data/crawl/20260829/strips/manifest.json \
      --filter data/crawl/20260829/filter_report.json \
      --out data/crawl/20260829/colcrop [--limit N] [--files a.jpg,b.jpg] [--anno]
"""
import argparse
import json
import os
import sys

import cv2
import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

COL_POS = ["万", "千", "百", "十", "个"]
_GRID_THS = (195, 210, 225, 238, 248)


# ---------------------------------------------------------------- 掩码
def blogger_ink_mask(a):
    """博主彩色墨迹：高饱和非白非暗；或暗而低饱和（黑笔）。返回 bool(H,W)。"""
    a = a.astype(np.int16)
    mn = a.min(2)
    mx = a.max(2)
    mean = a.sum(2) / 3.0
    sat = mx - mn
    return ((sat > 70) & (mx > 110) & (mean > 40) & (mean < 245)) | ((mean < 160) & (sat < 140))


def load_rgb(path):
    img = cv2.imread(path)
    if img is None:
        raise FileNotFoundError(path)
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


# ---------------------------------------------------------------- 竖向几何（格线锚定）
def detect_gridlines(gray, xa, xb, y0, y1, minrun=0.85, maxthick=6):
    """细横向格线。行=最长连续暗 run≥minrun×(xb-xa)；把连续暗行按 ≤2px 步长并成带，带厚≤maxthick
    才算线（剔除博主满高色带/整行底纹）。多阈值并集后 ±5 聚类。返回升序 y 列表。"""
    H, W = gray.shape
    xa, xb = max(0, xa), min(W, xb)
    y0, y1 = max(0, y0), min(H, y1)
    all_rows = set()
    for thr in _GRID_THS:
        seg = (gray < thr).astype(np.uint8)
        rows = []
        for y in range(y0, y1):
            best = cur = 0
            for x in range(xa, xb):
                if seg[y, x]:
                    cur += 1
                    if cur > best:
                        best = cur
                else:
                    cur = 0
            if best >= minrun * (xb - xa):
                rows.append(y)
        i = 0
        while i < len(rows):
            j = i
            while j + 1 < len(rows) and rows[j + 1] - rows[j] <= 2:
                j += 1
            if rows[j] - rows[i] + 1 <= maxthick:
                all_rows.add(int(round(np.mean(rows[i:j + 1]))))
            i = j + 1
    out = []
    for y in sorted(all_rows):
        if not out or y - out[-1] > 5:
            out.append(y)
    return out


def resolve_band_rows(ty, pitch, lines):
    """由格线定两整格。返回 (band_top, g, cell_bot, method)。

    g = 比 ty 小的最大格线 = 目标格顶(上期/目标分界)。
    band_top = 最接近 g−pitch 且 < g−0.6pitch 的格线(上一期格顶) —— 最近邻+下限切法自动跳过
               孪生双线(idx62 1704/1710、idx22 1503)与中继线。
    cell_bot = 最接近 g+pitch 且 > g+0.6pitch 的格线(目标格底)。
    无格线时 band_top=ty−1.5p、cell_bot 由 g 推导 → method='pitch-fallback'（几何不可信,报告标注）。
    """
    cand = [l for l in lines if l < ty]
    if not cand:
        return int(round(ty - 1.5 * pitch)), int(round(ty - 0.5 * pitch)), None, "pitch-fallback"
    g = max(cand)
    prev_cand = [l for l in lines if l <= g - 0.6 * pitch]
    band_top = min(prev_cand, key=lambda l: abs(l - (g - pitch))) if prev_cand else None
    if band_top is None:
        band_top = int(round(ty - 1.5 * pitch))
        cell_bot = None
        return band_top, g, cell_bot, "pitch-fallback"
    bot_cand = [l for l in lines if l >= g + 0.6 * pitch]
    cell_bot = min(bot_cand, key=lambda l: abs(l - (g + pitch))) if bot_cand else None
    return int(band_top), g, cell_bot, "gridline"


def cols_usable(cols, img_w):
    """列锚能否当逐位列窗用：5 个、间距近等距、右端在表内中右。返回 (bool, reason)。"""
    valid = sorted(int(round(c)) for c in (cols or []) if c and c > 0)
    if len(valid) < 5:
        return False, f"列数{len(valid)}<5"
    gaps = [valid[i + 1] - valid[i] for i in range(len(valid) - 1)]
    p = float(np.median(gaps))
    if any(g < 0.35 * p or g > 1.9 * p for g in gaps):
        return False, "间距非等距(期号/和值列混入)"
    if valid[-1] < img_w * 0.5:
        return False, f"右端{valid[-1]}过窄(比例尺错)"
    if p < 40:
        return False, "列距过小"
    return True, ""


# ---------------------------------------------------------------- 博主标记（目标格内）
def target_cell_marks(img, y0s, cell_bot, pitch):
    """目标格内博主彩色标记。返回 (comps_abs, wash_excluded)。
    comps_abs: [(x,y,w,h,a)] 全图坐标；过滤：起点须在格内(≤cell_bot)，剔除整区高水印/色带、
    贴窗竖线、细横线、满宽块、噪点。"""
    H, W = img.shape[:2]
    y1s = int(round(min(H, (cell_bot if cell_bot else y0s + pitch) + 0.9 * pitch)))
    reg = img[y0s:y1s, :]
    RH, RW = reg.shape[:2]
    cell_bot_rel = (cell_bot if cell_bot else y0s + pitch) - y0s
    m = blogger_ink_mask(reg).astype(np.uint8) * 255
    n, _, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    comps, wash = [], 0
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        if a < 100 or w < 7 or h < 7:
            continue
        if y >= cell_bot_rel - 2:          # 起点越过格底 = 下邻打印行彩色开奖字，非博主标记
            continue
        if y <= 2 and y + h >= RH - 2:     # 整区高水印/色带（触区顶&底）
            wash += 1
            continue
        if w < 6 and h > 0.9 * RH:
            continue
        if w > 2.2 * h and h < pitch * 0.55:   # 细横线/边框
            continue
        if w > 0.6 * RW:
            continue
        comps.append((int(x), int(y), int(w), int(h), int(a)))
    return comps, wash


def pick_per_position(comps, cols, y0s, shared, pitch, img_w):
    """5 列窗内挑博主标记（垂直中心最贴目标行中心 shared+0.5p）。返回 dict pos->bbox_abs 或 reason。
    列锚不可用时整条转 DS（dict 空）。"""
    if not cols_usable(cols, img_w)[0]:
        return {}
    valid = sorted(int(round(c)) for c in cols if c and c > 0)
    gaps = [valid[i + 1] - valid[i] for i in range(len(valid) - 1)]
    gap = float(np.median(gaps))
    bounds = [valid[0] - gap / 2] + [(valid[i] + valid[i + 1]) / 2 for i in range(len(valid) - 1)] \
        + [valid[-1] + gap / 2]
    center = shared + 0.5 * pitch
    out = {}
    for i, pos in enumerate(COL_POS):
        xl, xr = int(bounds[i]), int(bounds[i + 1])
        win = [q for q in comps if xl <= (q[0] + q[2] / 2) <= xr]
        if not win:
            continue
        win.sort(key=lambda q: abs((q[1] + y0s + q[3] / 2) - center))
        x, y, w, h, a = win[0]
        out[pos] = {"x": x, "y": y + y0s, "w": int(w), "h": int(h)}
    return out


# ---------------------------------------------------------------- 产出
def crop_band(img, geom, scale=3):
    """干净双行带裁剪（band_top..band_bot, 全宽, 放大 scale, 无参考线）。返回 (rgb_uint8, orig_rows)。"""
    y0, y1 = geom["band_top"], geom["band_bot"]
    band = img[y0:y1, :]
    up = cv2.resize(band, (band.shape[1] * scale, band.shape[0] * scale),
                    interpolation=cv2.INTER_CUBIC)
    return up, (y0, y1)


def annotate_band(band_up, geom, meta_label=""):
    """复核图：带顶(上期格顶)画绿线、上期/目标分界 g 画红线、顶部写标签。返回 PIL RGB。"""
    from PIL import Image, ImageDraw
    im = Image.fromarray(band_up).convert("RGB")
    d = ImageDraw.Draw(im)
    w, h = im.size
    d.line([(0, 0), (w, 0)], fill=(0, 180, 0), width=2)                    # 绿 = 带顶
    gy = int((geom["g"] - geom["band_top"]) * 3)
    d.line([(0, gy), (w, gy)], fill=(235, 30, 30), width=2)                # 红 = 上期/目标分界
    d.rectangle([0, 0, w, 20], fill=(8, 8, 8))
    d.text((4, 2), meta_label, fill=(255, 255, 255))
    return im


def adaptive_tile(img, box, min_h=120, margin_frac=0.25):
    """把标记 bbox(原图坐标, dict x/y/w/h)裁成自适应窗口：bbox+25%边距，再放大到目标高~min_h。"""
    H, W = img.shape[:2]
    mar = int(round(margin_frac * max(box["w"], box["h"])))
    xa, xb = max(0, box["x"] - mar), min(W, box["x"] + box["w"] + mar)
    ya, yb = max(0, box["y"] - mar), min(H, box["y"] + box["h"] + mar)
    if xb <= xa or yb <= ya:
        return None
    sub = img[ya:yb, xa:xb]
    sc = max(1, int(round(min_h / max(1, sub.shape[0]))))
    if sc > 1:
        sub = cv2.resize(sub, (sub.shape[1] * sc, sub.shape[0] * sc),
                         interpolation=cv2.INTER_CUBIC)
    return sub


def prev_content_span(img, y_top, y_bot, xa, xb, min_run=25):
    """上一期格内 [y_top,y_bot) 打印墨垂直范围(自检: 条顶有没有切到字)。
    返回 (top, bot) 绝对行号 或 None。x 窗=中段列带; 行须有 ≥min_run 连续暗像素才算内容行。"""
    g = img[..., :3].mean(2).astype(np.uint8)
    seg = (g < 170)
    rows = []
    for y in range(max(0, y_top), min(img.shape[0], y_bot)):
        run = cur = 0
        for x in range(xa, min(img.shape[1], xb)):
            if seg[y, x]:
                cur += 1
                if cur > run:
                    run = cur
            else:
                cur = 0
        if run >= min_run:
            rows.append(y)
    if not rows:
        return None
    return rows[0], rows[-1]


def process(img, img_h, file, meta, fr, out_dir, do_anno=False):
    """处理一张：返回 geometry dict 写入 crop_report。"""
    ty = meta.get("target_y") or (meta.get("y"))
    cols = meta.get("cols")
    pitch = float((fr or {}).get("row_pitch") or meta.get("row_pitch") or 105)
    gray = img.mean(2).astype(np.uint8)
    lines = detect_gridlines(gray, 0, img_h[1],
                             int(ty - 2.4 * pitch), int(ty + 1.3 * pitch))
    band_top, g, cell_bot, method = resolve_band_rows(ty, pitch, lines)
    cell_bot_real = cell_bot if cell_bot is not None else int(round(g + pitch))
    comps, wash = target_cell_marks(img, g, cell_bot_real, pitch)
    mark_low = max((g + y + h for _, y, _, h, _ in comps), default=cell_bot_real)
    band_bot = int(max(ty + 0.5 * pitch, cell_bot_real, mark_low + 14))
    band_top, band_bot = max(0, band_top), min(img_h[0], band_bot)

    usable, why = cols_usable(cols, img_h[1])
    pos = pick_per_position(comps, cols, g, g, pitch, img_h[1]) if usable else {}

    # 自检：上一期格内 [band_top+1, g) 打印墨垂直范围。若顶贴 band_top(条顶) 或为 None(整格无墨)
    # ⇒ 该格不是上一期打印行格(格线/期号错位)，人眼需复核 anno 图。纯报告不 gate。
    span = prev_content_span(img, band_top + 1, g, int(img_h[1] * 0.06),
                             int(img_h[1] * 0.94))
    geom = {"file": file, "method": method,
            "target_y": int(ty), "row_pitch": pitch,
            "gridlines": lines,
            "band_top": band_top, "g": int(g), "cell_bot": cell_bot_real,
            "mark_low": mark_low, "band_bot": band_bot,
            "cols_ok": usable, "cols_reason": why,
            "n_marks_comp": len(comps), "n_wash_excluded": wash,
            "prev_dark_span": None if span is None else {"top": span[0], "bot": span[1]},
            "prev_top_margin": None if span is None else int(span[0] - band_top),
            "positions": pos}
    stem = os.path.splitext(file)[0]

    band_up, _ = crop_band(img, geom)
    cv2.imwrite(os.path.join(out_dir, f"{stem}_band.png"), band_up[:, :, ::-1])
    if do_anno:
        lab = f"idx? [{file[:24]}] [{method}] 条[y {band_top},{band_bot}) 上期格[{band_top},{g}) 目标格[{g},{cell_bot_real})"
        annotate_band(band_up, geom, lab).save(os.path.join(out_dir, f"{stem}_band_anno.png"))
    # 逐位自适应窗口
    for pos, box in pos.items():
        t = adaptive_tile(img, box)
        if t is None:
            continue
        cv2.imwrite(os.path.join(out_dir, f"{stem}_{pos}.png"), t[:, :, ::-1])
    return geom


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--images", required=True, help="原图目录")
    ap.add_argument("--manifest", required=True, help="strips/manifest.json（含 cols/target_y）")
    ap.add_argument("--filter", required=True, help="filter_report.json（含 row_pitch）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--files", default="", help="只处理这些文件(逗号分隔, 可带路径/后缀)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--anno", action="store_true", help="另出带参考线的复核图")
    args = ap.parse_args()

    man = json.load(open(args.manifest, encoding="utf-8"))
    fr = json.load(open(args.filter, encoding="utf-8"))["images"]
    os.makedirs(args.out, exist_ok=True)

    want = set()
    if args.files:
        want = {os.path.basename(s) for s in args.files.split(",") if s.strip()}

    geoms = {}
    done = 0
    for file, meta in (man["images"] or {}).items():
        if want and file not in want:
            continue
        if not (meta and meta.get("ok") and meta.get("target_y")):
            geoms[file] = {"file": file, "ok": False, "reason": "manifest 无 ok/target_y"}
            continue
        ip = os.path.join(args.images, file)
        if not os.path.exists(ip):
            base = os.path.splitext(file)[0]
            for ext in (".jpg", ".png", ".jpeg"):
                alt = os.path.join(args.images, base + ext)
                if os.path.exists(alt):
                    ip = alt
                    break
        try:
            img = load_rgb(ip)
        except Exception:
            geoms[file] = {"file": file, "ok": False, "reason": "缺图"}
            continue
        g = process(img, img.shape[:2], file, meta, fr.get(file) or {},
                    args.out, do_anno=args.anno)
        g["ok"] = True
        geoms[file] = g
        done += 1
        if args.limit and done >= args.limit:
            break

    with open(os.path.join(args.out, "crop_report.json"), "w", encoding="utf-8") as f:
        json.dump({"date": args.date, "n": done,
                   "generated_by": "col_band_crop.py",
                   "images": geoms}, f, ensure_ascii=False, indent=1)
    ok = sum(1 for g in geoms.values() if g.get("ok"))
    grid = sum(1 for g in geoms.values() if g.get("method") == "gridline")
    print(f"处理 {done} 张 → band {ok}（格线锚定 {grid} / 旧公式回退 {ok - grid}）"
          f" → {args.out}/crop_report.json")


if __name__ == "__main__":
    main()
