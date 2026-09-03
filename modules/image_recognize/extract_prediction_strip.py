#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""extract_prediction_strip.py — 确定性裁「目标期行窄条」（只含 5 数字列，剔除期号/和值列）。

用途：给 read_blogger_prediction 喂窄条，让 GLM 读博主目标期行手写数字时不必解析整张
652×2179 大图（全图 80s → 窄条 5.4s，15×提速）。窄条从左到右 = 万 千 百 十 个。

定位：
  - 目标期行 y：优先 filter_report.period_pairs 里期号==target_period 的 row；
    否则 calib_period+row_pitch 推下一行；否则最后一个数据行带；再不行 → 返回 None。
  - 5 数字列：目标期行带内 find_cols_in_band 找列簇 → 剔除最左期号列（期号文字，其后有大
    空档）→ 用剩余数字列的等间距推全 5 列（含无预测的空白列）→ 裁全 5 列跨度。

输出：strpis/<date>/<stem>_strip.png（放大 3×）每张一条；manifest.json 记录
  {file, target_y, strip_crop:(x0,y0,x1,y1), cols:[5 个 x 中心], ok, reason}。

用法:
  python3 modules/image_recognize/extract_prediction_strip.py --date 20260829 \
      --filter data/crawl/20260829/filter_report.json \
      --gate data/crawl/20260829/blogger_hit_gate.json \
      --images data/crawl/20260829/images \
      --target-period 26231 --calib-period 26230 --calib-draw "9 4 6 8 3" \
      --out data/crawl/20260829/strips
"""
import argparse
import json
import os
import sys

import cv2
import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _REPO)

from modules.image_recognize.cv_trend_reader.analyze_grid import find_cols_in_band  # noqa: E402
from modules.image_recognize.cv_trend_reader.reader import _cnn_backend  # noqa: E402

N_DIGIT_COLS = 5
COL_POS = ["万", "千", "百", "十", "个"]


def read_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def load(path):
    img = cv2.imread(path)
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def target_y_from(ent, target_period, calib_period, row_pitch):
    """返回 (target_y, source)。source ∈ period/calib/band/None。"""
    for p in ent.get("period_pairs") or []:
        if p.get("period") == target_period and p.get("row") is not None:
            return p["row"], "period"
    for p in ent.get("period_pairs") or []:
        if p.get("period") == calib_period and p.get("row") is not None:
            return p["row"] + (row_pitch or 0), "calib"
    return None, None


def digit_cols_from_band(band, min_w=18, gap=18):
    """目标期行带内找列簇，剔除最左期号列，返回 5 个数字列 (x0,x1) 或 ""。"""
    cols = find_cols_in_band(band, 0, band.shape[0], min_w=min_w, gap=gap)
    if len(cols) < 2:
        return None
    # 期号列 = 最左，且与其右邻列之间有空档（数字列是等距紧排的）
    # 用非期号的最大列簇间距作为 pitch；期号列通常独立且靠左
    # 简化：丢最左簇，若它右边缘到次左簇左边缘 > 1.2×(median 数字列间距) → 是期号列
    centers = [ (a + b) // 2 for a, b in cols ]
    # 数字列间距估计：相邻簇中心差的中位数
    gaps = [centers[i + 1] - centers[i] for i in range(len(centers) - 1)]
    if not gaps:
        return None
    gaps_sorted = sorted(gaps)
    pitch = gaps_sorted[len(gaps_sorted) // 2] if gaps_sorted else 0
    # 丢期号列：最左簇与右邻簇间距明显 > pitch（期号列独立靠左）
    start = 0
    if len(cols) > 1 and (centers[1] - centers[0]) > 1.3 * max(pitch, 1):
        start = 1
    digit = cols[start:]
    if not digit:
        return None
    return digit


def expand_to_5(digit_ranges, img_w):
    """用数字列中位数间距补齐到 5 列（含预测空白的列）。返回 [(x0,x1)*5, ...]。"""
    centers = sorted((a + b) / 2 for a, b in digit_ranges)
    if len(centers) >= 2:
        gaps = [centers[i + 1] - centers[i] for i in range(len(centers) - 1)]
        pitch = float(np.median(gaps))
    else:
        pitch = 0.0

    e = list(centers)
    # 以中位数间距向两端补全，回到 5 列（含博主没写的空白列）
    while len(e) < 5 and pitch > 1:
        leftmost = min(e)
        if leftmost - pitch > 0:
            e.append(leftmost - pitch)
        elif max(e) + pitch < img_w:
            e.append(max(e) + pitch)
        else:
            break
        e.sort()
    if len(e) < 5:
        return None

    # 收尾：若超过 5 列（误多），取前 5（数字列从左到右应按序）
    e = sorted(e[:5])
    half = pitch / 2 if pitch > 1 else 45
    out = []
    for c in e:
        out.append((int(max(0, c - half)), int(min(img_w, c + half))))
    return out


def cols_to_ranges(centers, img_w):
    """从已校准的 5 个开奖列中心（filter_report.cols，全图灰度投影法，抗手写干扰）
    生成 [(x0,x1)*5]。缺列时按中位间距外推到 5 列，超多则截前 5。不足 2 列 → None。"""
    centers = sorted(int(round(c)) for c in centers if c and c > 0)
    if len(centers) < 2:
        return None
    gaps = [centers[i + 1] - centers[i] for i in range(len(centers) - 1)]
    pitch = float(np.median(gaps)) if gaps else 0
    e = list(centers)
    while len(e) < 5 and pitch > 1:
        leftmost, rightmost = min(e), max(e)
        if leftmost - pitch > 0:
            e.append(leftmost - pitch)
        elif rightmost + pitch < img_w:
            e.append(rightmost + pitch)
        else:
            break
        e.sort()
    if len(e) < 2:
        return None
    e = sorted(e[:5])
    half = pitch / 2 if pitch > 1 else 45
    out = []
    for c in e:
        out.append((int(max(0, c - half)), int(min(img_w, c + half))))
    return out


def _read_col_digit(gray, cx, half, predict, model):
    """在已知列中心 cx 处 CNN 读单个打印数字。窗口 [cx-half, cx+half] 直接送 digit_cnn
    （内部会 resize 到 32×48）。返回 int digit 或 None。

    打印数字比博主手写规整，窄条带里在列中心附近几乎就是该位数字，逐列读远比全带连通域
    切分稳（后者会把左侧期号列"26233"当 5 位主串、漏掉右侧打印数字）。
    """
    x0 = max(0, int(cx) - half)
    x1 = min(gray.shape[1], int(cx) + half)
    if x1 - x0 < 8:
        return None
    r = predict(gray[:, x0:x1], model=model)
    return int(r[0]) if r else None


def cnn_anchor_cols(img, ent, calib_period, calib_draw, row_pitch):
    """改动 A：用上一期(calib)权威开奖行 CNN 逐列读 + 对拍，钉死 5 列中心（全图 x 坐标）。

    根治 ① 的启发式列位偏移：detect_columns 是灰度投影"挑最等距 5 列"，会因期号/和值列
    多出一个峰而整串右移。这里改用**上一期已知开奖**（权威 5 位串）做几何锚：
    以启发式 cols 为基，生成 3 个候选集（本体 / 丢最左右补1列 / 左补1列丢最右），逐列 CNN
    读数字与 calib_draw 对拍，取 (匹配数高, 间距最均匀) 者 → 那 5 个 x 中心即 ground truth
    列位。

    间距均匀性做平局裁决：期号列混入时 [期号,万,千,百,十] 与 [万,千,百,十,个] 会都只对上
    4/5（重叠 4 位），但前者首 gap 远大于 pitch，用 gap 标准差最小者破平局。
    失败（找不到 calib 行/对不齐）→ None，退回旧启发式。
    """
    if isinstance(calib_draw, str):
        calib_draw = [int(x) for x in calib_draw.split() if x.strip().lstrip('-').isdigit()]
    if not calib_draw or len(calib_draw) != 5:
        return None
    calib_y = None
    for p in ent.get("period_pairs") or []:
        if str(p.get("period")) == str(calib_period) and p.get("row") is not None:
            calib_y = p["row"]
            break
    if calib_y is None:
        return None
    half = max(ent.get("row_half") or 0, (row_pitch or 0) // 2, 55)
    y0 = max(0, int(calib_y) - half)
    y1 = min(img.shape[0], int(calib_y) + half)
    band = img[y0:y1]
    if band.shape[0] < 12 or band.shape[1] < 12:
        return None
    back = _cnn_backend()
    if back is None:
        return None
    model, predict = back
    gray = cv2.cvtColor(band, cv2.COLOR_RGB2GRAY)
    base = sorted(int(c) for c in (ent.get("cols") or []) if c)
    if len(base) < 5:
        return None
    gaps = [base[i + 1] - base[i] for i in range(len(base) - 1)]
    pitch = int(np.median(gaps)) if gaps else 0
    if pitch <= 1:
        return None
    hw = max(20, pitch // 2)

    def gap_cost(cs):
        g = [cs[i + 1] - cs[i] for i in range(4)]
        return float(np.std(g)) if g else float("inf")

    def score(cs):
        """读一组 5 列中心，返回 (匹配数, -gap_cost)。OOB/读空→该位无匹配。"""
        ds = [_read_col_digit(gray, c, hw, predict, model) for c in cs]
        m = sum(1 for d, a in zip(ds, calib_draw) if d == a)
        return m, -gap_cost(cs)

    # 3 个候选集：base 本体 + 左右各错开 1 列（覆盖期号列混入→整串偏移 1 格）。
    # 用 base 实际值（非 pitch 平移）避免 base 间距不齐时产生交错近重复列。
    variants = [base]
    variants.append(base[1:] + [base[-1] + pitch])   # 期号在左：丢最左，右补 1 列
    variants.append([base[0] - pitch] + base[:-1])   # 期号在右：左补 1 列，丢最右
    best = None  # ((matches, -gap_cost), centers)
    for cs in variants:
        if any(c < 0 or c >= gray.shape[1] for c in cs):
            continue
        m, gc = score(cs)
        if best is None or (m, gc) > best[0]:
            best = ((m, gc), cs)
    if best is None or best[0][0] < 4:  # 至少 4/5 对拍一致才信
        return None
    return best[1]


def measure_full_width(band, centers):
    """改动 B：用强色标注掩码（saturation_mask 同款：max-min>80 且 max>120）测博主彩色
    笔迹覆盖了几列。打印数字是暗色/低饱和（灰度），博主彩标(红/紫/蓝)高饱和 → 掩码只圈
    博主笔迹。返回 int(0..5)。"""
    if band is None or centers is None or len(centers) < 5:
        return 0
    b = band[..., 0].astype(np.int16)
    g = band[..., 1].astype(np.int16)
    r = band[..., 2].astype(np.int16)
    mm = np.maximum(np.maximum(b, g), r)
    mn = np.minimum(np.minimum(b, g), r)
    sat = (((mm - mn) > 80) & (mm > 120)).astype(np.uint8)
    cs = sorted(int(c) for c in centers)
    gaps = [cs[i + 1] - cs[i] for i in range(4)]
    pitch = int(np.median(gaps)) if gaps else 0
    half = pitch // 2 if pitch > 1 else 30
    n = 0
    for c in cs:
        x0 = max(0, c - half)
        x1 = min(band.shape[1], c + half)
        if x1 <= x0:
            continue
        if int(sat[:, x0:x1].sum()) >= 3:
            n += 1
    return n


def extract(img, ent, target_period, calib_period, calib_draw, images_dir, file):
    """返回 (strip_img, meta) 或 (None, meta)。

    优先裁「5 数字列窄条」（快，GLM ~9s）；列检测失败则回退「全宽行带」（稳，GLM ~31s）。
    strip_type ∈ cols / row；reader 按类型选 prompt。
    """
    row_pitch = ent.get("row_pitch") or 0
    ty, src = target_y_from(ent, target_period, calib_period, row_pitch)
    if ty is None:
        return None, {"file": file, "ok": False, "reason": "目标期行未定位", "target_y": None}
    half = max(ent.get("row_half") or 0, row_pitch // 2 if row_pitch else 0, 55)
    y0, y1 = max(0, ty - half), min(img.shape[0], ty + half)
    band = img[y0:y1]

    def upscale(a):
        return cv2.resize(a, (a.shape[1] * 3, a.shape[0] * 3),
                          interpolation=cv2.INTER_CUBIC)

    # 改动 A：优先 CNN 锚定列位（上一期权威开奖对拍）；失败退回旧启发式 filter_report.cols。
    anchor_cols = cnn_anchor_cols(img, ent, calib_period, calib_draw, row_pitch)
    if anchor_cols:
        ranges = cols_to_ranges(anchor_cols, img.shape[1])
        col_anchor = "cnn-calib"
    else:
        ranges = cols_to_ranges(ent.get("cols") or [], img.shape[1])
        col_anchor = "heuristic"
    if ranges is None:
        digit = digit_cols_from_band(band)
        ranges = expand_to_5(digit, img.shape[1]) if digit else None
        col_anchor = "band" if ranges else "none"
    if ranges:
        x0 = max(0, ranges[0][0] - 6)
        x1 = min(img.shape[1], ranges[-1][1] + 6)
        strip = band[:, x0:x1]
        centers = [(a + b) // 2 for a, b in ranges]
        full_width = measure_full_width(band, centers)
        return upscale(strip), {"file": file, "ok": True, "target_y": ty, "source": src,
                                "strip_type": "cols", "y_range": [int(y0), int(y1)],
                                "x_range": [int(x0), int(x1)], "cols": centers,
                                "col_anchor": col_anchor, "full_width": full_width,
                                "col_positions": COL_POS, "n_cols": len(ranges)}
    # 回退：全宽行带（含期号列），靠 GLM 自行锚定 期号列+5数字列；无列中心，full_width 不测
    strip = band[:, :]
    return upscale(strip), {"file": file, "ok": True, "target_y": ty, "source": src,
                            "strip_type": "row", "y_range": [int(y0), int(y1)],
                            "x_range": [0, int(img.shape[1])], "cols": None,
                            "col_anchor": "none", "full_width": None,
                            "col_positions": COL_POS, "n_cols": 0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--filter", required=True)
    ap.add_argument("--gate", required=True)
    ap.add_argument("--images", required=True)
    ap.add_argument("--target-period", required=True)
    ap.add_argument("--calib-period", default="")
    ap.add_argument("--calib-draw", default="",
                    help="上一期(calib)权威开奖 5 位，如 '1 6 3 4 0'；改动 A 用它 CNN 对拍钉死列位")
    ap.add_argument("--out", required=True, help="strips 输出目录")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    report = read_json(args.filter)
    gate = read_json(args.gate)
    imgs = report["images"]
    os.makedirs(args.out, exist_ok=True)

    # 只裁 gate=pass 的图（真·候选）
    passes = [v for v in gate["images"].values() if v.get("gate") == "pass"]
    if args.limit:
        passes = passes[:args.limit]

    manifests = {}
    n_ok = n_fail = 0
    for v in passes:
        file = v["file"]
        ent = imgs.get(file, {})
        ip = os.path.join(args.images, file)
        if not os.path.exists(ip):
            manifests[file] = {"file": file, "ok": False, "reason": "缺图"}
            n_fail += 1
            continue
        img = load(ip)
        strip, meta = extract(img, ent, args.target_period, args.calib_period,
                              args.calib_draw, args.images, file)
        manifests[file] = meta
        if strip is None:
            n_fail += 1
            continue
        stem = os.path.splitext(file)[0]
        sp = os.path.join(args.out, f"{stem}_strip.png")
        cv2.imwrite(sp, strip[:, :, ::-1])
        meta["strip"] = os.path.basename(sp)
        meta["y"] = meta.get("target_y")
        n_ok += 1

    man = {"date": args.date, "target_period": args.target_period,
           "calib_period": args.calib_period, "n_pass": len(passes),
           "n_strips": n_ok, "n_fail": n_fail,
           "generated_by": "extract_prediction_strip.py",
           "说明": "裁目标期行窄条(5数字列，剔期号列)，供 GLM 读；从左到右=万千百十个",
           "images": manifests}
    with open(os.path.join(args.out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
    print(f"gate pass {len(passes)} → 裁出窄条 {n_ok} / 失败 {n_fail} → {args.out}/manifest.json")


if __name__ == "__main__":
    main()
