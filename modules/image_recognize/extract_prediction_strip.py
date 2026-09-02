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
      --target-period 26231 --calib-period 26230 \
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


def extract(img, ent, target_period, calib_period, images_dir, file):
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

    # 先试 5 数字列窄条：优先已校准的 filter_report.cols（全图灰度投影，抗手写彩色数字干扰，
    # 实测均匀等距；digit_cols_from_band 会在博主手写数字上偏移/跳列）。缺列再退化到 band 检测。
    ranges = cols_to_ranges(ent.get("cols") or [], img.shape[1])
    if ranges is None:
        digit = digit_cols_from_band(band)
        ranges = expand_to_5(digit, img.shape[1]) if digit else None
    if ranges:
        x0 = max(0, ranges[0][0] - 6)
        x1 = min(img.shape[1], ranges[-1][1] + 6)
        strip = band[:, x0:x1]
        centers = [(a + b) // 2 for a, b in ranges]
        return upscale(strip), {"file": file, "ok": True, "target_y": ty, "source": src,
                                "strip_type": "cols", "y_range": [int(y0), int(y1)],
                                "x_range": [int(x0), int(x1)], "cols": centers,
                                "col_positions": COL_POS, "n_cols": len(ranges)}
    # 回退：全宽行带（含期号列），靠 GLM 自行锚定 期号列+5数字列
    strip = band[:, :]
    return upscale(strip), {"file": file, "ok": True, "target_y": ty, "source": src,
                            "strip_type": "row", "y_range": [int(y0), int(y1)],
                            "x_range": [0, int(img.shape[1])], "cols": None,
                            "col_positions": COL_POS, "n_cols": 0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--filter", required=True)
    ap.add_argument("--gate", required=True)
    ap.add_argument("--images", required=True)
    ap.add_argument("--target-period", required=True)
    ap.add_argument("--calib-period", default="")
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
                              args.images, file)
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
