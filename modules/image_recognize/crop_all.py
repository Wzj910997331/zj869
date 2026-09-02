#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
crop_all.py — 全字色走势图标注区批量裁剪（黑/红/蓝/绿字各自处理，纯 OpenCV 秒级）。

用户意图（2026-09-01）：非绿字走势图占 2/3，纯视觉定位太慢（90min+），
目标 <5min 处理 300+ 张。博主画的规律标注（色带/线/圈）永远是高饱和有色图形，
与数字颜色无关 → 规律区域 = 饱和像素连通区可确定性检出。

按字色分类各自处理（实测 359 张分布 绿45/红44/蓝18/黑249）：
1. 用哪种字色掩码能检出最规整的数字行带 → 判定该图字色
2. 用该字色掩码做行网格（水平投影 → 行带 → 相位行梳）
3. 饱和掩码做标注行检测（行窗内饱和像素 > 200）
4. 裁剪标注行 → 01_rows / 02_annotated / 03_debug（与绿字路径同格式）

输出 data/recognize/<date>_all/{rank:03d}_{sizeKB}_{stem}/ + crops_all_manifest.json
每 50 张刷盘，纯 OpenCV 全程 <1min（359 张实测 ~2s）。

用法：
  /usr/bin/python3 modules/image_recognize/crop_all.py --date 20260830 [--limit N] [--out ...]
"""
import argparse
import glob
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import cv2
import numpy as np

from common import REPO, load_json, write_json, fix_print

GRID_X0, GRID_X1 = 280, 1000   # 网格区默认 x（1024 宽图）
GRID_YMAX = 2200               # 数字区下界
ROW_ANNO_THRESH = 200          # 行窗内饱和像素数 > 此值 = 标注行
ROW_HALF = 68                  # 行条半高
STACK_WIDTH = 640              # 行条栈目标宽度
STACK_MAX_H = 2200             # 行条栈最大高度

COLOR_MASKS = {
    # 绿字走势图（白底绿字）原掩码；黑/红/蓝字各自掩码
    "green": lambda b, g, r: (g > 150) & (r < 210) & (b < 150),
    "red": lambda b, g, r: (r > 180) & (g < 120) & (b < 120),
    "blue": lambda b, g, r: (b > 180) & (r < 120) & (g < 120),
    "dark": lambda b, g, r: (np.minimum(np.minimum(r, g), b) < 150),
}


def classify_digit_color(b, g, r):
    """用哪种字色掩码能检出最规整数字行带 → 判定字色（绿/红/蓝/黑）。"""
    w = b.shape[1]
    xr = (max(0, int(0.2 * w)), w)   # 投影范围自适应图宽（右 80% 数字区）
    best, bs = None, 0.0
    for name, fn in COLOR_MASKS.items():
        m = cv2.morphologyEx(fn(b, g, r).astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        proj = m[:, xr[0]:xr[1]].sum(axis=1).astype(int)
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
        if inb and len(active) - y0 >= 15:
            bands.append((y0, len(active) - 1))
        lens = [bb - aa for aa, bb in bands]
        h = np.median(lens) if lens else 0
        n = len(bands)
        score = n if 15 <= h <= 95 else n * 0.3
        if score > bs:
            best, bs = name, score
    return best, bs


def detect_rows(mask, h=None):
    """行带分段 → 行网格。投影范围自适应图宽（右侧 80% 数字区）。

    标准竖版大图（行距~136）用相位行梳补全到 GRID_YMAX（兼容 1024×2200 版式）；
    紧凑图（行距<100，如 321×378）用实际行距在图高内补全——不再 fallback 出图外虚拟行；
    稀疏竖版图（bands 少但 h>1.5w，红字稀疏标注图）兜底 pitch=136 虚拟行保住标注检测；
    横图/近方形图 bands 少 → 无有效行网格（no-grid）。
    返回 rows（行槽中心 y 列表）、pitch。"""
    w = mask.shape[1]
    proj = mask[:, max(0, int(0.2 * w)):w].sum(axis=1).astype(int)
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
    if inb and len(active) - y0 >= 15:
        bands.append((y0, len(active) - 1))
    if not bands:
        return [], 0
    centers = [(a + b) // 2 for a, b in bands]
    diffs = [bb - aa for aa, bb in zip(centers, centers[1:])]
    ok136 = [d for d in diffs if 100 <= d <= 170]
    if len(ok136) >= 3:
        # 标准竖版大图：相位行梳补全到 GRID_YMAX（保持原逻辑）
        pitch = int(np.median(ok136))
        residues = [c % pitch for c in centers]
        residues = [r if r <= pitch * 0.7 else r - pitch for r in residues]
        phase = int(np.median(residues)) % pitch
        rows = []
        k = (400 - phase + pitch - 1) // pitch
        while True:
            y = phase + k * pitch
            if y > GRID_YMAX:
                break
            rows.append(y)
            k += 1
        return rows, pitch
    # 紧凑/小图：用实际行距，在图高内补全（从首行前一段上探）
    if diffs and all(20 <= d <= 300 for d in diffs) and len(centers) >= 2:
        pitch = int(np.median(diffs))
        ymax = min(GRID_YMAX, h if h else GRID_YMAX)
        phase = centers[0] % pitch
        rows = []
        k = (max(0, centers[0] - pitch) - phase + pitch - 1) // pitch
        while True:
            y = phase + k * pitch
            if y > ymax:
                break
            if y >= 0:
                rows.append(y)
            k += 1
        if rows:
            return rows, pitch
        return centers, pitch
    # 稀疏竖版图：bands 少但竖版走势图（h>1.5w），兜底 pitch=136 虚拟行，保住博主标注的检出
    if h and h > 1.5 * w and centers:
        pitch = 136
        residues = [c % pitch for c in centers]
        residues = [r if r <= pitch * 0.7 else r - pitch for r in residues]
        phase = int(np.median(residues)) % pitch
        rows = []
        k = (400 - phase + pitch - 1) // pitch
        while True:
            y = phase + k * pitch
            if y > GRID_YMAX:
                break
            rows.append(y)
            k += 1
        return rows, pitch
    # 横图/近方形且行带太少 → 无有效行网格
    return [], 0


def detect_digit_x_range(mask, rows):
    """数字列 x 范围：数字区垂直投影的列峰外包络 ±45px，钳制在图内。"""
    if rows:
        y0 = max(0, rows[0] - ROW_HALF)
        y1 = min(mask.shape[0], rows[-1] + ROW_HALF)
    else:
        y0, y1 = 400, GRID_YMAX
    sub = mask[max(0, y0):y1, :]
    if sub.size == 0:
        return GRID_X0, GRID_X1
    vproj = sub.sum(axis=0).astype(float)
    w = mask.shape[1]
    xs = np.where(vproj > 0.05 * vproj.max())[0]
    if len(xs) < 3:
        return max(10, int(0.30 * w)), min(w, int(0.98 * w))
    return max(0, int(xs.min()) - 45), min(w, int(xs.max()) + 45)


def saturation_mask(b, g, r):
    """强色标注掩码：max-min > 80 且 max > 120。"""
    mm = np.maximum(np.maximum(b, g), r)
    mn = np.minimum(np.minimum(b, g), r)
    return (((mm - mn) > 80) & (mm > 120)).astype(np.uint8)


def annotated_rows(sat, rows, filled, x0, x1, row_half=ROW_HALF):
    """行窗内饱和像素数 > 阈值 → 标注行集合；空 footer 行排除。"""
    out = {}
    for i, y in enumerate(rows):
        if i < len(filled) and not filled[i]:
            out[i] = 0
            continue
        out[i] = int(sat[max(0, y - row_half):y + row_half, x0:x1].sum())
    return [i for i, c in out.items() if c > ROW_ANNO_THRESH], out


def filled_rows(mask, rows, x0, x1, row_half=ROW_HALF):
    """行窗内字色像素数 > 阈值 → 该行有内容。"""
    return [int(mask[max(0, y - row_half):y + row_half, x0:x1].sum()) > 600 for y in rows]


def build_stack(items, width):
    """行条按行号竖排 → 带红色行标签的栈图（对齐绿字路径 02_annotated 格式）。"""
    if not items:
        return None
    ordered = sorted(items, key=lambda t: t[0])
    imgs = [t[1] for t in ordered]
    H = sum(i.shape[0] for i in imgs)
    W = imgs[0].shape[1]
    stack = np.full((H, W, 3), 255, np.uint8)
    y = 0
    for (row, im) in ordered:
        stack[y:y + im.shape[0], :, :] = im
        cv2.putText(stack, f"row{row}", (6, y + 26),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2)
        y += im.shape[0]
    scale = STACK_WIDTH / W
    nh = int(round(H * scale))
    if nh > STACK_MAX_H:
        scale = STACK_MAX_H / H
        nh = STACK_MAX_H
        nw = int(round(W * scale))
    else:
        nw = STACK_WIDTH
    return cv2.resize(stack, (nw, nh), interpolation=cv2.INTER_AREA)


def process_one(path):
    """单图：字色分类 + 行检测 + 标注行检测。返回 (status, info, rows)。"""
    img = cv2.imread(path)
    if img is None:
        return "unreadable", {}, []
    h, w = img.shape[:2]
    b, g, r = img[..., 0].astype(np.int16), img[..., 1].astype(np.int16), img[..., 2].astype(np.int16)
    color, score = classify_digit_color(b, g, r)
    if color is None:
        return "no-grid", {"image_size": [h, w]}, []
    m = cv2.morphologyEx(COLOR_MASKS[color](b, g, r).astype(np.uint8),
                         cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    rows, pitch = detect_rows(m, h)
    if not rows:
        return "no-grid", {"image_size": [h, w], "digit_color": color}, []
    x0, x1 = detect_digit_x_range(m, rows)
    # 行窗半高随行距缩放：紧凑图（行距<136）避免相邻行窗重叠，大图保持 ROW_HALF=68
    row_half = min(ROW_HALF, max(12, int(pitch * 0.45)))
    sat = saturation_mask(b, g, r)
    filled = filled_rows(m, rows, x0, x1, row_half)
    annotated, sat_counts = annotated_rows(sat, rows, filled, x0, x1, row_half)
    info = {"image_size": [h, w], "digit_color": color, "digit_mask_score": round(score, 1),
            "row_pitch": pitch, "n_rows": len(rows),
            "grid_x": [x0, x1], "annotated_rows": annotated, "n_annotated": len(annotated),
            "row_half": row_half, "row_sat": {str(i): c for i, c in sat_counts.items()}}
    if annotated:
        return "cropped", info, {"rows": rows, "x0": x0, "x1": x1, "filled": filled}
    return "no-anno", info, {"rows": rows, "x0": x0, "x1": x1, "filled": filled}


def build_exclude_list(results):
    """从 manifest 派生剔除清单：仅 status==cropped 进识别，其余（no-anno/no-grid/error）全部剔除。
    后续处理只需读 exclude_list.json['excluded']（或直接过滤 manifest 的 status=='cropped'）。"""
    excluded = {}
    for name, rec in results["images"].items():
        st = rec.get("status")
        if st == "cropped":
            continue
        note = {
            "no-anno": "无博主标注行（行网格正常，无高饱和图形）",
            "no-grid": "非行网格图（小尺寸/非走势图版式）",
            "error": "处理异常",
        }.get(st, st)
        excluded[name] = {"reason": st, "note": note,
                          "size_kb": rec.get("size_kb"),
                          "digit_color": rec.get("digit_color"),
                          "image_size": rec.get("image_size")}
    return {"date": results["date"],
            "generated_by": "crop_all.py",
            "rule": "仅 status==cropped 进识别；no-anno/no-grid/error 全部剔除",
            "n_excluded": len(excluded),
            "excluded": excluded}


def write_exclude_list(results, out_root):
    """exclude_list.json 落盘，与 manifest 同目录。"""
    path = os.path.join(out_root, "exclude_list.json")
    write_json(build_exclude_list(results), path)
    return path


def process_one_ranked(rank, path, out_root):
    """单图全流程(线程 worker): 字色分类 + 行网格 + 标注行检测 + 裁剪落盘。
    与串行版逐位一致 —— rank 预分配(enumerate 序), 目录名/字段稳定; 各图写独立目录, 线程安全。"""
    name = os.path.basename(path)
    sz = os.path.getsize(path)
    rec = {"file": name, "size_bytes": sz, "size_kb": sz // 1024}
    try:
        status, info, grid = process_one(path)
        rec.update(info)
        rec["status"] = status
        if status == "cropped":
            d = os.path.join(out_root, f"{rank:03d}_{sz // 1024}KB_{os.path.splitext(name)[0]}")
            os.makedirs(d, exist_ok=True)
            img = cv2.imread(path)
            rows, x0, x1 = grid["rows"], grid["x0"], grid["x1"]
            filled = grid.get("filled")
            row_half = rec.get("row_half", ROW_HALF)
            strips = []
            for i in rec["annotated_rows"]:
                y = int(rows[i])
                y0, y1 = max(0, y - row_half), min(img.shape[0], y + row_half)
                strips.append((i, img[y0:y1, x0:x1].copy()))
            anno_stack = build_stack(strips, x1 - x0)
            if anno_stack is not None:
                cv2.imwrite(os.path.join(d, "02_annotated.png"), anno_stack)
            # 全行栈(对齐绿字路径 01_rows): 只取有内容行, 越界行排除
            if filled is None:
                filled = filled_rows(
                    cv2.morphologyEx(
                        COLOR_MASKS[rec["digit_color"]](*cv2.split(img)).astype(np.uint8),
                        cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)), rows, x0, x1)
            filled_idx = [i for i in range(len(rows)) if i < len(filled) and filled[i]]
            full_strips = [(i, img[max(0, int(rows[i]) - row_half):min(img.shape[0], int(rows[i]) + row_half), x0:x1].copy())
                           for i in filled_idx]
            full_stack = build_stack(full_strips, x1 - x0)
            if full_stack is not None:
                cv2.imwrite(os.path.join(d, "01_rows.png"), full_stack)
            # debug: 原图画标注行框
            dbg = img.copy()
            for i in rec["annotated_rows"]:
                y = int(rows[i])
                cv2.rectangle(dbg, (x0, max(0, y - row_half)), (x1, min(img.shape[0], y + row_half)),
                              (255, 0, 0), 3)
                cv2.putText(dbg, f"row{i}", (x0 + 6, max(24, y - row_half + 28)),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 0, 0), 2)
            cv2.imwrite(os.path.join(d, "03_debug.png"), dbg)
            rec["crop_dir"] = os.path.relpath(d, out_root)
    except Exception as e:
        rec["status"] = "error"
        rec["error"] = str(e)[:200]
    return rank, name, rec


def main():
    fix_print()
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=16,
                    help="多线程并发数(默认 16; 纯 OpenCV/numpy 释放 GIL, 可并行)")
    ap.add_argument("--filter", default=None,
                    help="filter_report.json 路径: 只裁剪 keep/uncertain 图"
                         "(即②确定后待送视觉的数量, 排除 283 张已确定性剔除的废图)")
    args = ap.parse_args()

    img_dir = os.path.join(REPO, "data", "crawl", args.date, "images")
    # 白名单正则防临时图污染：仅 s_2_<uuid>_<n>.(png|jpg|jpeg)（*.loc*.jpg 等测试临时图一律排除）
    src_pat = re.compile(r"^s_2_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}_\d+\.(png|jpg|jpeg)$")
    files = [os.path.join(img_dir, f) for f in os.listdir(img_dir) if src_pat.match(f)]
    if not files:
        print(f"[crop_all] 无图片: {img_dir}")
        sys.exit(2)
    files.sort(key=lambda f: os.path.getsize(f), reverse=True)
    n_all = len(files)
    if args.filter:
        fr = load_json(args.filter) or {}
        if not fr.get("images"):
            print(f"[crop_all] 读不到 filter_report images: {args.filter}")
            sys.exit(2)
        keep = {f for f, r in fr["images"].items()
                if (r.get("decision") or "").startswith(("keep", "uncertain"))}
        files = [p for p in files if os.path.basename(p) in keep]
        print(f"[crop_all] --filter: {len(keep)} 张 keep/uncertain 送视觉, "
              f"源目录命中 {len(files)} 张 (剔除 {n_all - len(files)} 张 filter 已排除)",
              flush=True)
    if args.limit:
        files = files[:args.limit]
    out_root = args.out or os.path.join(REPO, "data", "recognize", f"{args.date}_all")
    os.makedirs(out_root, exist_ok=True)

    results = {"date": args.date, "sorted_by": "file_size_desc", "n_total": len(files), "images": {}}
    if args.filter:
        results["n_source_images"] = n_all
        results["filtered_by"] = args.filter
    t0 = time.time()
    done = cropped = no_anno = no_grid = failed = 0
    colors = {}
    prog_every = max(1, min(25, len(files) // 10))   # 每 ~25 张打一次进度(小样本自适配)
    rank_name = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(process_one_ranked, rank, path, out_root): (rank, path)
                for rank, path in enumerate(files, 1)}
        for fut in as_completed(futs):
            rank, name, rec = fut.result()
            rank_name.append((rank, name))
            results["images"][name] = rec
            st = rec.get("status")
            if st == "cropped":
                cropped += 1
            elif st == "no-anno":
                no_anno += 1
            elif st == "no-grid":
                no_grid += 1
            else:
                failed += 1
            colors[rec.get("digit_color", "?")] = colors.get(rec.get("digit_color", "?"), 0) + 1
            done += 1
            if done % prog_every == 0 or done == len(files):
                el = time.time() - t0
                print(f"[crop_all] 进度 {done}/{len(files)} ({el:.0f}s, "
                      f"{done / el:.1f} 张/s) cropped={cropped} | "
                      f"{name[:36]} {rec['size_kb']}KB 字色{rec.get('digit_color', '?')} "
                      f"行{rec.get('n_rows', '?')} 标注{rec.get('n_annotated', '?')} -> {st}",
                      flush=True)
            if done % 50 == 0:
                results["_progress"] = {"done": done, "cropped": cropped, "no_anno": no_anno,
                                        "no_grid": no_grid, "failed": failed,
                                        "elapsed_s": round(time.time() - t0, 1)}
                write_json(results, os.path.join(out_root, "crops_all_manifest.json"))

    # 按 rank 序重建 images(与串行版按尺寸排序的输出一致)
    results["images"] = {n: results["images"][n] for _, n in sorted(rank_name)}
    results["_progress"] = {"done": done, "cropped": cropped, "no_anno": no_anno,
                            "no_grid": no_grid, "failed": failed,
                            "elapsed_s": round(time.time() - t0, 1)}
    results["digit_colors"] = colors
    write_json(results, os.path.join(out_root, "crops_all_manifest.json"))
    excl_path = write_exclude_list(results, out_root)
    print(f"[crop_all] DONE {done}/{len(files)} cropped={cropped} no_anno={no_anno} "
          f"no_grid={no_grid} failed={failed} {time.time()-t0:.1f}s")
    print(f"[crop_all] 字色分布: {colors}")
    print(f"[crop_all] -> {os.path.join(out_root, 'crops_all_manifest.json')}")
    print(f"[crop_all] -> {excl_path}（剔除 {results['n_total'] - cropped} 张）")


if __name__ == "__main__":
    main()
