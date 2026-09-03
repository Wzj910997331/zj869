#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""filter_trend.py — ① 定位 + 切分（v5：A 路判走势图 → 过滤无标注 → B 路期号锚 → 横切整块窄条）。

用户意图(2026-09-03 v5): 过滤阶段三步：
  ① 判走势图：A 路(OpenCV 网格/行结构, 内容无关, process_one 承载) + B 路(CNN 期号锚)
  ② 过滤无标注：process_one no-anno → 排除（博主没画规律，送视觉纯浪费）
  ③ 切窄条：横切整块（含上一期 + 目标期，期号列 + 5 开奖列）→ 交给视觉模型

核心简化(v4/v5)：切窄条是**横着切**，不需要列对齐 → 去掉「钉死 5 列 x」；
开奖对拍降级为**仅记录**（detect_columns 投影列位系统性 ±1 偏移使多期对拍无法区分
「列位偏」和「期号读错」→ 门槛误剔，默认 CALIB_MIN_OK=0）。
窄条含上一期行，视觉模型靠上一期开奖数字自己对齐列位。

v5 B 路：期号读法 = **逐行期号格隔离放大 OCR**（_isolate_left_digits + _read_period_cell），
不是宽条 OCR（宽条混列 → 小/密真图读成乱码）。隔离失败走宽条兜底(=v4 行为, 不回退)。

输出：
  data/crawl/<date>/strips/<stem>_strip.png   # 横切整块窄条（3×放大）
  data/crawl/<date>/strips/manifest.json      # 窄条清单（read_blogger_prediction 消费）
  data/crawl/<date>/filter_report.json        # 定位结果 + decision（调试 + read fr 依赖）

用法:
  /usr/bin/python3 modules/image_recognize/filter_trend.py \
    --date 20260831 --target-period 26232 \
    --lottery data/crawl/20260831/lottery_recent.json [--limit N]
"""
import argparse
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from common import REPO, load_json, write_json, fix_print  # noqa: E402
from crop_all import process_one  # noqa: E402
from cv_trend_reader.reader import load, ocr_digits, _cnn_backend, cnn_available  # noqa: E402
from cv_trend_reader.analyze_grid import match_period  # noqa: E402
from cv_trend_reader.calib_anchor import match_row_draw  # noqa: E402

# 白名单正则防临时图污染(与 crop_all 同口径)
SRC_PAT = re.compile(
    r"^s_2_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}_\d+\.(png|jpg|jpeg)$")

# 期号 OCR 组合: (psm, upscale, threshold)。
# 关键: 期号数字多为浅灰(亮度~150-235), 阈值必须够高(180~235)才抓得到; 低阈值保底深色字。
PERIOD_COMBOS = [(7, 6, 210), (7, 9, 235), (7, 12, 220), (7, 8, 180), (7, 6, 120), (8, 8, 210)]

# ---- v3: 灰度投影列定位参数(仅作读开奖数字的候选起点, 不参与切窄条) ----
GRAY_THR = 205              # 灰度掩码阈值
COL_WIDTH_MIN = 8           # 投影宽峰最小宽度
COL_VCAP = 0.6              # 峰高上限(剔高密度块/面板)
PEAK_TH = 0.12              # 归一化投影峰阈值
RUN_GAP = 5                 # 峰合并间隔

# ---- v4: 多期开奖对拍阈值(默认值, 可 --lookback/--min-ok/--match-th 覆盖) ----
CALIB_LOOKBACK = 10         # 对拍倒数的期数(读底部 N 行)
CALIB_MIN_OK = 0            # 开奖对拍硬门槛: 0=关闭(期号定位为主, 对拍仅记录); ≥1=至少几期 ≥match-th 才切
CALIB_MATCH_TH = 3          # 单期开奖对拍匹配位数阈值(≥3/5 算这行对)

UPSCALE = 3                 # 窄条放大倍数

# 期号格隔离: 最左数字簇最大相对宽(超过=左侧不是独立期号格, 如密集多列表 → 放弃隔离走宽条)
CELL_MAX_W_FRAC = 0.16


def detect_columns(img, info, grid):
    """v3 列定位: 灰度<thr 掩码全域投影 → 宽峰(剔高密度块/窄网格线) → 选最等距 5 列中心。

    v4 里仅作「读开奖数字在哪读」的候选起点(base_cols)，不参与切窄条(横切整块不需要列对齐)。
    返回 5 个列中心(升序, 全图 x 坐标); 失败返回 []。"""
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


def _isolate_left_digits(band_gray):
    """逐行: 去横网格线 → 最左数字簇 (x0,x1)。返回 None 表示无/不可信。

    期号 = 每行最左的独立数字串(打印字)。先扣横向长线(网格边框), 再连通域取最左一簇,
    向右合并间隔 < 2.2×中位字宽的组件(覆盖 5 位连写 "26232"), 到第一个大空档停。
    簇宽超过 CELL_MAX_W_FRAC×图宽 → 左侧是密集多列(非独立期号格) → None。
    """
    h, w = band_gray.shape[:2]
    _, bw = cv2.threshold(band_gray, 205, 255, cv2.THRESH_BINARY_INV)
    horiz = cv2.morphologyEx(bw, cv2.MORPH_OPEN,
                             cv2.getStructuringElement(cv2.MORPH_RECT, (max(30, w // 3), 1)))
    bw2 = cv2.subtract(bw, horiz)
    sub = bw2[:, :int(w * 0.5)]
    if sub.size == 0:
        return None
    n, lab, st, ct = cv2.connectedComponentsWithStats(sub, 8)
    comps = []
    for i in range(1, n):
        x, y, ww, hh, area = st[i]
        if area < 20 or ww < 4 or hh < 4:
            continue
        if y <= 1 or y + hh >= sub.shape[0] - 1:  # 触顶/触底=横线残余
            continue
        comps.append((x, y, ww, hh, area))
    if not comps:
        return None
    comps.sort(key=lambda c: c[0])
    widths = sorted(c[2] for c in comps)
    med_w = widths[len(widths) // 2] if widths else 8
    gap_lim = max(6, int(med_w * 2.2))
    x0 = comps[0][0]
    x1 = comps[0][0] + comps[0][2]
    cur_end = x1
    for c in comps[1:]:
        if c[0] - cur_end <= gap_lim:
            x1 = max(x1, c[0] + c[2])
            cur_end = x1
        else:
            break
    if x1 - x0 > CELL_MAX_W_FRAC * w:
        return None
    return (int(x0), int(x1))


def _read_period_cell(img, y_c, row_half, engine=None):
    """逐行读期号: 期号格隔离 tight crop → 放大 OCR。返回 4..6 位数字串或 ''。

    隔离失败(读不到/簇不可信) → 返回 ''，由调用方走宽条兜底。"""
    h, w = img.shape[:2]
    y0, y1 = max(0, int(y_c) - row_half), min(h, int(y_c) + row_half)
    if y1 - y0 < 10:
        return ''
    band = img[y0:y1, :]
    gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
    rr = _isolate_left_digits(gray)
    if not rr:
        return ''
    x0, x1 = rr
    roi = band[:, x0:min(x1 + 8, w)]
    if roi.shape[1] < 10:
        return ''
    for up, thr in ((8, 205), (10, 210), (6, 180), (6, 235)):
        try:
            res = ocr_digits(roi, psm=7, upscale=up, threshold=thr, engine=engine)
        except Exception:
            continue
        s = "".join(str(d) for d, _ in res)
        if 4 <= len(s) <= 6:
            return s
    return ''


def period_pairs(img, rows, row_half, lottery, engine=None, n_rows=6):
    """读底部多行期号, 与开奖历史做匹配。返回 [{row, period, period_full, matched}]。

    v5: 期号主路 = 逐行期号格隔离放大(不混列, 救小/密真图); 隔离读不出再宽条兜底。
    n_rows 参数化; engine: None=取环境(auto/默认); 显式 cnn/tesseract 强制该引擎。"""
    h, w = img.shape[:2]
    x1_wide = max(200, int(w * 0.32))
    pairs = []
    for y_c in reversed(list(rows)[-n_rows:]):
        y_c = int(y_c)
        if y_c < 0 or y_c >= h:
            continue
        best = _read_period_cell(img, y_c, row_half, engine=engine)
        if not best:  # 兜底: 宽条组合(v4 行为, 防隔离误伤正常图)
            y0, y1 = max(0, y_c - row_half), min(h, y_c + row_half)
            if y1 - y0 < 8:
                continue
            roi = img[y0:y1, 0:x1_wide]
            if roi.size == 0:
                continue
            for psm, up, thr in PERIOD_COMBOS:
                try:
                    res = ocr_digits(roi, psm=psm, upscale=up, threshold=thr, engine=engine)
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


def calib_match_multi(img, rows, row_half, lottery, draw_map, base_cols,
                      predict, model, params):
    """多期开奖对拍：读底部 N 行期号 + 每行 5 开奖数字对拍权威开奖。

    返回 (ok_count, pairs)。pairs 每项加 calib_match 字段（该行对拍匹配位数 0–5，读不到 -1）。
    ok_count = 对拍 ≥match_th 的期数，≥min_ok 说明定位正确（定位错行不会有多期能对上）。
    """
    pairs = period_pairs(img, rows, row_half, lottery, engine=None,
                         n_rows=params["CALIB_LOOKBACK"])
    ok_count = 0
    for p in pairs:
        p["calib_match"] = -1
        if not p["matched"] or p["row"] is None:
            continue
        draw = draw_map.get(p["period_full"])
        if not draw:
            continue
        y_c = int(p["row"])
        band = img[max(0, y_c - row_half): min(img.shape[0], y_c + row_half)]
        if band.shape[0] < 12:
            continue
        gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
        m = match_row_draw(gray, base_cols, draw, predict, model)
        p["calib_match"] = m
        if m >= params["CALIB_MATCH_TH"]:
            ok_count += 1
    return ok_count, pairs


def locate_calib_row(pairs, calib_period, target_period, row_pitch):
    """从 pairs 找上一期(calib)行的 y。按优先级：
      calib 直接命中 → target 命中(上一行) → calib-1 命中(下一行)。找不到返回 None。"""
    c, t, cp = str(calib_period), str(target_period), str(int(calib_period) - 1)
    for p in pairs:
        if p.get("matched") and p.get("period_full") == c and p.get("row") is not None:
            return p["row"]
    for p in pairs:
        if p.get("matched") and p.get("period_full") == t and p.get("row") is not None:
            return p["row"] - row_pitch
    for p in pairs:
        if p.get("matched") and p.get("period_full") == cp and p.get("row") is not None:
            return p["row"] + row_pitch
    return None


def classify_image(img, path, lottery, draw_map, target_period, params, out_dir, file):
    """单图三步：① 判走势图(多期对拍) → ② 过滤无标注 → ③ 切窄条。

    返回 (decision, meta)。decision ∈ keep / exclude；keep 时已写窄条，meta 含窄条路径。
    """
    h, w = img.shape[:2]
    base = {"file": file, "image_size": [h, w]}
    try:
        status, pinfo, grid = process_one(path)
    except Exception as e:
        return "exclude", {**base, "reason": "error", "error": str(e)[:150]}
    for k in ("digit_color", "n_rows", "row_pitch", "n_annotated",
              "annotated_rows", "grid_x", "row_half"):
        if k in pinfo:
            base[k] = pinfo[k]

    # ② 过滤无标注 + 判走势图版式（process_one 已判）
    if status == "unreadable":
        return "exclude", {**base, "reason": "unreadable"}
    if status == "no-grid":
        return "exclude", {**base, "reason": "no-chart"}
    if status == "no-anno":
        return "exclude", {**base, "reason": "no-anno"}

    # status == "cropped"（有标注的走势图）继续
    rows = grid.get("rows") or []
    row_half = pinfo.get("row_half", 68)
    row_pitch = pinfo.get("row_pitch") or 0
    if not rows or row_pitch <= 1:
        return "exclude", {**base, "reason": "no-chart"}

    # 投影列位（仅作读开奖数字的候选起点，不参与切窄条）
    try:
        base_cols = detect_columns(img, pinfo, grid)
    except Exception:
        base_cols = []

    back = _cnn_backend()
    if back is None:
        return "exclude", {**base, "reason": "cnn-unavailable"}
    model, predict = back

    # ① 多期开奖对拍（读每行 5 开奖数字对拍权威开奖，仅作记录/可选弱验证）
    ok_count, pairs = calib_match_multi(img, rows, row_half, lottery, draw_map,
                                        base_cols, predict, model, params)
    base["period_pairs"] = pairs
    base["calib_ok_count"] = ok_count
    # 开奖对拍依赖 detect_columns 投影列位，而列位不可靠（期号/和值列混入 → 整串偏移），
    # 导致「列位置偏」是系统性的、对所有期都读错列 —— 多期对拍无法区分「列位偏」和「期号读错」，
    # 反而把期号定位正确的图误剔。故默认 CALIB_MIN_OK=0（对拍仅记录，不设门槛）；
    # 期号定位(locate_calib_row)才是「能否切」的唯一判据。想启用弱验证可传 --min-ok ≥1。
    if params["CALIB_MIN_OK"] > 0 and ok_count < params["CALIB_MIN_OK"]:
        return "exclude", {**base, "reason": "calib-fail",
                           "calib_ok_count": ok_count}

    # 纵向定位上一期行 → 目标期行
    calib_period = str(int(target_period) - 1)
    calib_row = locate_calib_row(pairs, calib_period, target_period, row_pitch)
    if calib_row is None:
        return "exclude", {**base, "reason": "目标期行未定位",
                           "calib_ok_count": ok_count}
    target_row = calib_row + row_pitch

    # ③ 切窄条（横切整块，含上一期 + 目标期，期号列 + 5 开奖列）
    x0, x1 = pinfo.get("grid_x", [0, w])
    y0 = max(0, int(calib_row) - row_half)
    y1 = min(h, int(target_row) + row_half)
    if y1 - y0 < row_half:
        return "exclude", {**base, "reason": "目标期行越界"}
    strip = img[y0:y1, x0:x1]
    strip = cv2.resize(strip, (strip.shape[1] * UPSCALE, strip.shape[0] * UPSCALE),
                       interpolation=cv2.INTER_CUBIC)
    stem = os.path.splitext(file)[0]
    sp = os.path.join(out_dir, f"{stem}_strip.png")
    cv2.imwrite(sp, strip)

    calib_draw = draw_map.get(calib_period, [])
    meta = {**base, "ok": True, "target_y": int(target_row), "calib_y": int(calib_row),
            "strip_type": "rows", "y_range": [int(y0), int(y1)], "x_range": [int(x0), int(x1)],
            "calib_draw": " ".join(map(str, calib_draw)), "calib_match": ok_count,
            "n_rows_in_strip": 2, "strip": os.path.basename(sp)}
    return "keep", meta


def classify_one_worker(p, lottery, draw_map, target_period, params, out_dir):
    """单图分类 + 切窄条(线程 worker)。load + classify 都在 worker 内做。"""
    file = os.path.basename(p)
    try:
        img = load(p)
        if img is None:
            return "exclude", {"file": file, "reason": "unreadable", "image_size": [0, 0]}
        return classify_image(img, p, lottery, draw_map, target_period, params, out_dir, file)
    except Exception as e:
        return "exclude", {"file": file, "reason": "error", "error": str(e)[:150]}


def main():
    fix_print()
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--target-period", required=True)
    ap.add_argument("--lottery", required=True)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out-dir", help="窄条输出目录，默认 data/crawl/<date>/strips")
    ap.add_argument("--lookback", type=int, default=CALIB_LOOKBACK, help="多期对拍倒数的期数")
    ap.add_argument("--min-ok", type=int, default=CALIB_MIN_OK,
                    help="开奖对拍硬门槛: 0=关闭(默认, 期号定位为主); ≥1=至少几期 ≥match-th 才切")
    ap.add_argument("--match-th", type=int, default=CALIB_MATCH_TH, help="单期开奖对拍匹配位数阈值(≥此算这行对)")
    ap.add_argument("--out", help="filter_report 输出路径(默认 data/crawl/<date>/filter_report.json)")
    args = ap.parse_args()

    lottery = load_json(args.lottery) or []
    if not lottery:
        print(f"[filter] ERROR: 读不到 lottery: {args.lottery}")
        sys.exit(2)
    draw_map = {str(rec["period"]): [int(x) for x in rec["numbers"]] for rec in lottery}

    if not cnn_available():
        print("[filter] ERROR: digit_cnn 模型不可用（主路固定 CNN）")
        sys.exit(2)

    params = {"CALIB_LOOKBACK": args.lookback, "CALIB_MIN_OK": args.min_ok,
              "CALIB_MATCH_TH": args.match_th}
    calib_period = str(int(args.target_period) - 1)

    img_dir = os.path.join(REPO, "data", "crawl", args.date, "images")
    files = sorted(f for f in os.listdir(img_dir) if SRC_PAT.match(f))
    if not files:
        print(f"[filter] 无图片: {img_dir}")
        sys.exit(2)
    if args.limit:
        files = files[:args.limit]

    out_dir = args.out_dir or os.path.join(REPO, "data", "crawl", args.date, "strips")
    os.makedirs(out_dir, exist_ok=True)

    t0 = time.time()
    prog_every = max(1, min(25, len(files) // 10))
    results, done = {}, 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(classify_one_worker, os.path.join(img_dir, f),
                          lottery, draw_map, args.target_period, params, out_dir): f for f in files}
        for fut in as_completed(futs):
            f = futs[fut]
            decision, meta = fut.result()
            results[f] = {"decision": decision, **meta}
            done += 1
            if done % prog_every == 0 or done == len(files):
                el = time.time() - t0
                print(f"[filter] 进度 {done}/{len(files)} ({el:.0f}s, "
                      f"{done / el:.1f} 张/s) ...", flush=True)

    # 按 files 顺序落盘: filter_report(全部) + manifest(keep 窄条)
    report_images, manifest_images = {}, {}
    summary = {"keep": 0, "exclude": {}}
    for f in files:
        info = results[f]
        decision = info["decision"]
        report_images[f] = info
        if decision == "keep":
            summary["keep"] += 1
            manifest_images[f] = info
        else:
            r = info.get("reason", "?")
            summary["exclude"][r] = summary["exclude"].get(r, 0) + 1

    report = {
        "date": args.date, "target_period": args.target_period,
        "calib_period": calib_period, "n_images": len(files),
        "generated_by": "filter_trend.py v5",
        "params": params,
        "summary": summary,
        "images": report_images,
    }
    out = args.out or os.path.join(REPO, "data", "crawl", args.date, "filter_report.json")
    write_json(report, out)

    n_strips = len(manifest_images)
    manifest = {
        "date": args.date, "target_period": args.target_period,
        "calib_period": calib_period,
        "n_pass": n_strips, "n_strips": n_strips, "n_fail": len(files) - n_strips,
        "generated_by": "filter_trend.py v5",
        "说明": "横切整块窄条(含上一期+目标期, 期号列+5开奖列), 供视觉模型读; 靠上一期开奖数字对齐列位",
        "images": manifest_images,
    }
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        import json
        json.dump(manifest, f, ensure_ascii=False, indent=1)

    print(f"[filter] DONE {len(files)} 张 {time.time()-t0:.1f}s")
    print(f"[filter] summary: {summary}")
    print(f"[filter] 窄条 {n_strips} 张 → {out_dir}/manifest.json")
    print(f"[filter] report → {out}")


if __name__ == "__main__":
    main()
