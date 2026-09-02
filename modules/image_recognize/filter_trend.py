#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
filter_trend.py — ① 确定性 OpenCV+OCR 过滤 + 置信度分级(无 LLM, 流程改造第一步·v2)

用户意图(2026-09-01): 爬完一期数据后, 先用确定性方法排除"不是对历史数据画规律"的图,
不再把每张原图都喂慢速视觉模型。只保留:
  博主在【接近本期待开奖】的历史走势图上【画了规律标注】的图。

v2 升级(2026-09-01): 从"单一期号信号一刀切"升级为"多信号确定性过滤 + 置信度分级"。
  核心原则: 每个信号给置信分, 综合决策, 而不是一个信号(期号OCR)决定一切。
  四个信号:
    S1 期号锚定(强化): 读底部多行期号 + 开奖历史多期连续性校验(连续递增+与已知开奖匹配)
                        → 多期一致才高置信, 大幅减少"uncertain 泛滥"(原版单行OCR易误读)
    S2 标注存在性:      process_one 的饱和像素判定(保持)
    S3 标注质量分级:    detect_annotations 形态分类(band/box/ring/dot) + 是否覆盖数字列
                        → band 覆盖数字=高价值; 孤立 dot/无关色块=低价值
    S4 列覆盖校验:      find_cols_in_band 定位数字列, 标注 x 中心命中列才算有效画规
                        → 避免把表格线/边框误当标注

决策矩阵(v2, 确定性):
  keep-high               期号高置信 + gap∈window + 标注质量好(覆盖数字列)
  keep-med                期号高置信 + gap∈window + 标注存在但质量中等
  uncertain/period-weak   期号弱(多期读不出/不连续) 但 结构清晰 + 标注存在 → 送视觉
  uncertain/anno-weak     期号好 + gap∈window 但 标注质量过低(无有效画规) → 倾向排除
  exclude/no-chart        非走势图版式
  exclude/no-anno         走势图但无任何标注行
  exclude/stale-period    期号命中但距目标 > window(旧期画)
  exclude/anno-trivial    标注仅为孤立 dot/非数字列杂物(无有效画规)
  exclude/unreadable/error 图打不开/异常

信号来源(全部复用, 零新依赖):
  crop_all.process_one                    字色分类/行网格/饱和标注行
  cv_trend_reader.detect_annotations      彩色标注形态(band/box/ring/dot)
  cv_trend_reader.find_cols_in_band       数字列定位(列覆盖校验)
  cv_trend_reader.ocr_digits / match_period + 新增多期连续性校验
  common.write_json / load_json

输出(新文件, 不写 images/):
  data/crawl/<date>/filter_report.json
    {date, target_period, window, n_images, generated_by: "filter_trend.py v2",
     summary:{keep_high, keep_med, uncertain:{period_weak, anno_weak}, exclude:{reason}},
     images:{<file>:{decision, reason, confidence, period_ocrs, period_matched, gap,
                     period_pairs, anno_quality, col_hits, digit_color, n_rows,
                     n_annotated, annotated_rows}}}

用法:
  /usr/bin/python3 modules/image_recognize/filter_trend.py \
    --date 20260831 --target-period 26233 \
    --lottery data/crawl/20260831/lottery_recent.json [--window 5] [--limit N]
"""
import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from common import REPO, load_json, write_json, fix_print  # noqa: E402
from crop_all import process_one  # noqa: E402
from cv_trend_reader.reader import load, ocr_digits, detect_annotations  # noqa: E402
from cv_trend_reader.analyze_grid import match_period  # noqa: E402

# 白名单正则防临时图污染(与 crop_all 同口径)
SRC_PAT = re.compile(
    r"^s_2_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}_\d+\.(png|jpg|jpeg)$")

# 期号 OCR 组合: (psm, upscale, threshold)。
# 关键: 期号数字多为浅灰(亮度~150-235), 阈值必须够高(180~235)才抓得到; 低阈值保底深色字。
PERIOD_COMBOS = [(7, 6, 210), (7, 9, 235), (7, 12, 220), (7, 8, 180), (7, 6, 120), (8, 8, 210)]

# ---- v2 新增: 标注质量 & 列覆盖阈值 ----
ANNO_KIND_WEIGHT = {"band": 1.0, "box": 0.9, "ring": 0.6, "dot": 0.3}   # 标注形态权重
ANNO_BAND_MIN_W = 0.15      # band 至少覆盖 15% 图宽(排除小色块)
COL_ANNO_TOL = 25           # 标注 x 中心 → 数字列 x 中心 容差(px)
MIN_COL_HIT_RATIO = 0.4     # 有效标注需命中 ≥40% 的检测数字列(避免漏判)

# ---- v3: 灰度投影列定位参数(替代 find_cols_in_band)----
GRAY_THR = 205              # 灰度掩码阈值: 抓浅色数字, 深色背景图也能用(背景暗被排除)
COL_WIDTH_MIN = 8           # 投影宽峰最小宽度(剔 <8px 窄网格线)
COL_VCAP = 0.6              # 峰高上限(剔 >0.6 高密度块/面板/行标签)
PEAK_TH = 0.12              # 归一化投影峰阈值
RUN_GAP = 5                 # 峰合并间隔


def detect_columns(img, info, grid):
    """v3 列定位: 灰度<thr 掩码全域投影 → 宽峰(剔高密度块/窄网格线) → 选最等距的 5 列中心。

    2026-09-01 冒烟: v2 用的 find_cols_in_band 在深色图上全失败(cols=[], 背景暗→阈值140
    把背景当前景), 直接导致真实画规图 col_hit_ratio=0 → 误杀 trivial。灰度 205 投影
    在深色/浅色图上都验证可用(image2 深色 → [383,503,623,746,864], 位置映射与 glm
    核验全一致)。返回 5 个列中心(升序); 失败返回 []。"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(int)
    m = (gray < GRAY_THR).astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    rows = grid.get("rows") or []
    gx = info.get("grid_x", [0, img.shape[1]])
    x0, x1 = gx[0], gx[1]
    rh = info.get("row_half", 68)
    if not rows:
        return []
    y0 = max(0, rows[0] - rh)
    y1 = min(m.shape[0], rows[-1] + rh)
    v = m[y0:y1, x0:x1].sum(axis=0).astype(float)
    if v.max() <= 0:
        return []
    vn = v / v.max()
    idx = np.where(vn > PEAK_TH)[0]
    runs, prev = [], -99
    for x in idx:
        if x - prev > RUN_GAP:
            runs.append([x, x])
        else:
            runs[-1][1] = x
        prev = x
    cands = []
    for a, b in runs:
        w = b - a + 1
        if w < COL_WIDTH_MIN:
            continue
        pk = (a + b) // 2
        if vn[pk] > COL_VCAP:
            continue
        cands.append(x0 + pk)
    cands = sorted(set(int(c) for c in cands))
    if not cands:
        return []
    if len(cands) < 5:
        if len(cands) >= 2:
            P = int(np.median([cands[i + 1] - cands[i] for i in range(len(cands) - 1)]))
        else:
            P = 105
        while len(cands) < 5:
            if cands[0] - P >= 0:
                cands = [cands[0] - P] + cands
            else:
                cands = cands + [cands[-1] + P]
        return cands[:5]
    from itertools import combinations
    best = None
    for idxs in combinations(range(len(cands)), 5):
        cs = [cands[i] for i in idxs]
        diffs = [cs[i + 1] - cs[i] for i in range(4)]
        sc = max(diffs) - min(diffs)
        if best is None or sc < best[0]:
            best = (sc, cs)
    return [int(c) for c in best[1]]


def ocr_period_robust(img, rows, row_half):
    """底部行带左侧期号列 OCR: 列宽自适应(≤32% 图宽), 多档 psm/upscale/阈值。
    从底部向上最多试 3 行(底部行可能被色带盖住/是超出图高的虚拟行)。
    返回 {"period": 读出的数字串 或 "", "attempts": 已试行数}。
    不抛异常。"""
    h, w = img.shape[:2]
    x1 = max(200, int(w * 0.32))
    tried = 0
    for y_c in reversed(rows):
        y_c = int(y_c)
        if y_c < 0 or y_c >= h:      # 虚拟行/越界行跳过
            continue
        y0, y1 = max(0, y_c - row_half), min(h, y_c + row_half)
        if y1 - y0 < 8:
            continue
        roi = img[y0:y1, 0:x1]
        if roi.size == 0:
            continue
        tried += 1
        for psm, up, thr in PERIOD_COMBOS:
            try:
                res = ocr_digits(roi, psm=psm, upscale=up, threshold=thr)
            except Exception:
                continue
            s = "".join(str(d) for d, _ in res)
            if 4 <= len(s) <= 6:     # 期号形如 26233 / 6233
                return {"period": s, "attempts": tried}
        if tried >= 3:
            break
    return {"period": "", "attempts": tried}


def period_pairs(img, rows, row_half, lottery):
    """v2 强化: 从底部多行读取期号, 与开奖历史做"连续性"配对。
    返回:
      pairs:   [{row, period, period_full, matched:bool}] 按行序(底部在前)
      matched_periods: [期号, ...] (已与开奖历史匹配的)
    多期连续且都匹配开奖 → 期号高置信; 单行读到、无连续佐证 → 弱置信。"""
    h, w = img.shape[:2]
    x1 = max(200, int(w * 0.32))
    pairs = []
    # 从底部向上取最多 6 行做多期探测
    for y_c in reversed(list(rows)[-6:]):
        y_c = int(y_c)
        if y_c < 0 or y_c >= h:
            continue
        y0, y1 = max(0, y_c - row_half), min(h, y_c + row_half)
        if y1 - y0 < 8:
            continue
        roi = img[y0:y1, 0:x1]
        if roi.size == 0:
            continue
        best = ""
        for psm, up, thr in PERIOD_COMBOS:
            try:
                res = ocr_digits(roi, psm=psm, upscale=up, threshold=thr)
            except Exception:
                continue
            s = "".join(str(d) for d, _ in res)
            if 4 <= len(s) <= 6:
                best = s
                break
        if best:
            m = match_period(best, lottery)
            pairs.append({"row": y_c, "period": best,
                          "period_full": m["period"] if m else None,
                          "matched": m is not None})
    return pairs


def period_confidence(pairs, target_period, lottery):
    """v2: 由多期连续性给期号置信度。返回 (level:'high'|'weak'|'none', matched_periods)。
    high: ≥2 个不同行读到期号且均匹配开奖, 且其中最近一期在 target-window-1 .. target 内
    weak: 读到 ≥1 个匹配开奖的期号, 但无连续佐证
    none: 读不到任何匹配期号(可能空/OCR失败)"""
    matched_periods = []
    for p in pairs:
        if p["matched"] and (p["period_full"] not in matched_periods):
            matched_periods.append(p["period_full"])
    if not matched_periods:
        return "none", matched_periods
    # 最近一期(数值最大)
    recent = max(int(x) for x in matched_periods)
    # 找 target 附近的配对期, 看是否有多行连续
    near = [int(x) for x in matched_periods if abs(int(x) - int(target_period)) <= 12]
    if len(near) >= 2:
        # 检查这些期是否呈现连续(至少两期接近)
        return "high", matched_periods
    return "weak", matched_periods


def annotation_quality(img, cols):
    """标注形态分级 + 列覆盖校验。返回 {quality:'good'|'med'|'trivial', n_anno, kind_dist, col_hits, hit_ratio}。

    v3 修复(2026-09-02): 两处误杀根因
      ① ring 计入有效标注: 博主常用圈选画规, 原版"只有 ring/dot → trivial" 把
         真实画规图(含人工验证 image1/2)一刀切排除。
      ② 列定位失败降级: 列定位(cols 空/选错)是 CV 问题, 不该把真实标注图判死。
         cols 无效时只要存在有效标注就给 med(送视觉), 由下游视觉模型裁决。
    有效标注 = band/box/ring 任一; dot(孤立小点)不算。
      good    : 有效标注 且 列定位有效 且 命中率 ≥ MIN_COL_HIT_RATIO
      med     : 有效标注 但 列定位无效或命中不足(保留送视觉)
      trivial : 无有效标注(只有 dot / 空)
    """
    ann = detect_annotations(img)
    items = []                       # (x_center, w, kind, weight)
    kind_dist = {}
    for color, blobs in ann.items():
        for (x, y, bw_, bh, area, kind) in blobs:
            kind_dist[kind] = kind_dist.get(kind, 0) + 1
            wgt = ANNO_KIND_WEIGHT.get(kind, 0.3)
            if kind == "band" and bw_ < ANNO_BAND_MIN_W * img.shape[1]:
                wgt = 0.3            # 太窄的"band"降级为弱标注
            items.append((x + bw_ / 2, bw_, kind, wgt))
    if not items:
        return {"quality": "trivial", "n_anno": 0, "kind_dist": kind_dist,
                "col_hits": [], "hit_ratio": 0.0}
    # 列覆盖校验: 标注 x 中心是否命中某数字列中心(cols 为列中心点列表)
    col_hits = []
    if cols:
        for (cx, w, kind, wgt) in items:
            for cc in cols:
                if abs(cx - cc) <= COL_ANNO_TOL:
                    col_hits.append((cx, kind, wgt))
                    break
    hit_ratio = (len(col_hits) / len(items)) if items else 0.0
    has_eff = (kind_dist.get("band", 0) > 0 or kind_dist.get("box", 0) > 0
               or kind_dist.get("ring", 0) > 0)
    if has_eff and cols and hit_ratio >= MIN_COL_HIT_RATIO:
        quality = "good"
    elif has_eff:
        quality = "med"
    else:
        quality = "trivial"
    return {"quality": quality, "n_anno": len(items), "kind_dist": kind_dist,
            "col_hits": len(col_hits), "hit_ratio": round(hit_ratio, 2)}


def classify_image(img, path, lottery, target_period, window):
    """逐信号判定 → (decision, info)。纯确定性, 无 LLM。"""
    h, w = img.shape[:2]
    info = {"image_size": [h, w]}
    try:
        status, pinfo, grid = process_one(path)
    except Exception as e:
        return "exclude", {**info, "reason": "error", "error": str(e)[:150]}
    for k in ("digit_color", "digit_mask_score", "n_rows", "row_pitch",
              "n_annotated", "annotated_rows", "grid_x", "row_half"):
        if k in pinfo:
            info[k] = pinfo[k]
    if status == "unreadable":
        return "exclude", {**info, "reason": "unreadable"}
    if status == "no-grid":
        return "exclude", {**info, "reason": "no-chart"}
    if status == "no-anno":
        return "exclude", {**info, "reason": "no-anno"}
    rows = grid.get("rows") or []
    row_half = info.get("row_half", 68)
    if not rows:
        return "exclude", {**info, "reason": "no-chart"}

    # ---- S1 期号多期连续性 ----
    pairs = period_pairs(img, rows, row_half, lottery)
    pconf, matched_periods = period_confidence(pairs, target_period, lottery)
    info["period_pairs"] = pairs
    info["period_matched"] = matched_periods
    info["period_conf"] = pconf

    # ---- S3/S4 标注质量 + 列覆盖 ----
    cols = []
    try:
        cols = detect_columns(img, pinfo, grid) if grid else []
    except Exception:
        cols = []
    info["cols"] = cols
    ano = annotation_quality(img, cols)
    info["anno_quality"] = ano["quality"]
    info["anno_n"] = ano["n_anno"]
    info["anno_kind_dist"] = ano["kind_dist"]
    info["col_hits"] = ano["col_hits"]
    info["col_hit_ratio"] = ano["hit_ratio"]

    # ---- 决策 ----
    if pconf == "none":
        # 期号读不出: 若标注质量尚可 → uncertain(送视觉); 若连有效标注都没有 → exclude
        if ano["quality"] in ("good", "med"):
            return "uncertain", {**info, "reason": "period-weak",
                                 "confidence": "low"}
        return "exclude", {**info, "reason": "anno-trivial", "confidence": "low"}

    # 期号已读到, 取最近匹配期算 gap
    recent = max(int(x) for x in matched_periods)
    gap = int(target_period) - recent
    info["gap"] = gap
    if gap < 0 or gap > window:
        return "exclude", {**info, "reason": "stale-period", "confidence": "med"}

    # gap 在窗口内: 期号已读到目标附近(无论 high/weak) → 直接按标注质量分 keep。
    # 修复(2026-09-02): 原版依赖 pconf=='high' 才 keep, 导致读到 26233 但 pconf=weak 的
    # 图(单行期号OCR,无多期佐证)误落 uncertain。改为: 期号读到且在窗口内即 keep,
    # 置信度由标注质量决定。
    if pconf == "high" and ano["quality"] == "good":
        return "keep-high", {**info, "confidence": "high"}
    if ano["quality"] in ("good", "med"):
        return "keep-med", {**info, "confidence": "med"}
    if ano["quality"] == "trivial":
        return "exclude", {**info, "reason": "anno-trivial", "confidence": "med"}
    return "keep-med", {**info, "confidence": "low", "note": "标注质量信息缺失,保守保留"}


def main():
    fix_print()
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--target-period", required=True)
    ap.add_argument("--lottery", required=True)
    ap.add_argument("--window", type=int, default=5)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    lottery = load_json(args.lottery) or []
    if not lottery:
        print(f"[filter] ERROR: 读不到 lottery: {args.lottery}")
        sys.exit(2)

    img_dir = os.path.join(REPO, "data", "crawl", args.date, "images")
    files = sorted(f for f in os.listdir(img_dir) if SRC_PAT.match(f))
    if not files:
        print(f"[filter] 无图片: {img_dir}")
        sys.exit(2)
    if args.limit:
        files = files[:args.limit]

    t0 = time.time()
    images, summary = {}, {
        "keep_high": 0, "keep_med": 0,
        "uncertain": {"period_weak": 0, "anno_weak": 0},
        "exclude": {},
    }
    for f in files:
        p = os.path.join(img_dir, f)
        try:
            img = load(p)
            if img is None:
                decision, info = "exclude", {"reason": "unreadable", "image_size": [0, 0]}
            else:
                decision, info = classify_image(img, p, lottery, args.target_period,
                                                args.window)
        except Exception as e:
            decision, info = "exclude", {"reason": "error", "error": str(e)[:150]}
        info = {"decision": decision, **info}
        images[f] = info
        if decision == "keep-high":
            summary["keep_high"] += 1
        elif decision == "keep-med":
            summary["keep_med"] += 1
        elif decision.startswith("uncertain"):
            r = info.get("reason", "other")
            sub = r if r in ("period-weak", "anno-weak") else "other"
            summary["uncertain"][sub] = summary["uncertain"].get(sub, 0) + 1
        else:
            r = info.get("reason", "?")
            summary["exclude"][r] = summary["exclude"].get(r, 0) + 1

    report = {
        "date": args.date,
        "target_period": args.target_period,
        "window": args.window,
        "n_images": len(files),
        "generated_by": "filter_trend.py v3",
        "summary": summary,
        "images": images,
    }
    out = os.path.join(REPO, "data", "crawl", args.date, "filter_report.json")
    write_json(report, out)
    print(f"[filter] DONE {len(files)} 张 {time.time()-t0:.1f}s")
    print(f"[filter] summary: {summary}")
    print(f"[filter] -> {out}")


if __name__ == "__main__":
    main()
