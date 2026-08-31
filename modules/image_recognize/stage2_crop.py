#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stage 2：规律区域检测 + 裁剪（替代早期 CNN 数字识别方案）

背景（实测 6 图验证）：
- 底图为绿色数字走势图；博主用**强色标注**（纯红/纯蓝等）在图上"画规律"：
  横带、圈X杀号、竖线、斜连线。底图自身只有低饱和色（白底、绿数字、
  浅青行底纹、灰网格线）。
- 因此"规律区域"= 高饱和度像素的连通区，无需识别数字即可确定性检出。
- 视觉大模型（glm-5.3-flash）对裁剪后的行条读数字可达 10/10 行 5/5 精确
  （对照 lottery 验证 26220-26229 全部命中），row0 锚定期用已知开奖校正。

本阶段产出（每图 crops/<img>/）：
- 01_rows.png       全行栈：全部有内容行按序竖排 + 红色行标签（row0..rowN）
- 02_annotated.png  标注行栈：仅博主标注的行（规律区域，快路径主输入）
- 03_debug.png      原图画裁剪框（人工核对用）
- crops_manifest.json 裁剪索引（标注行 / 行饱和量 / 两栈路径）

用法：
  /usr/bin/python3 modules/image_recognize/stage2_crop.py \
    --manifest data/recognize/<blogger>/<date>/manifest.json
"""
import argparse
import os
import sys

import cv2
import numpy as np

from common import COLUMN_TEMPLATE, load_json, write_json, fix_print

GRID_X0, GRID_X1 = 280, 1000   # 网格区（含 5 位置数字列，右缘边框线 x1015 不纳入）
GRID_Y0, GRID_Y1 = 430, 2160   # 数字区（表头之下，页脚之上）
STACK_WIDTH = 640              # 行条栈目标宽度（主输入，640 宽下数字 ~36px 模型可读）
STACK_MAX_H = 2200             # 行条栈最大高度
ROW_ANNO_THRESH = 200          # 行窗内饱和像素数 > 此值 = 标注行
ROW_HALF = 68                  # 行条半高（pitch≈136）


def saturation_mask(img):
    """强色标注掩码：max-min 通道差 > 80 且 max > 120。
    纯红(254,0,0)/纯蓝(2,0,254) 命中；白/浅青(202,249,240)/灰/绿数字全排除。"""
    b, g, r = img[..., 0].astype(np.int16), img[..., 1].astype(np.int16), img[..., 2].astype(np.int16)
    mm = np.maximum(np.maximum(b, g), r)
    mn = np.minimum(np.minimum(b, g), r)
    sat = ((mm - mn) > 80) & (mm > 120)
    return sat.astype(np.uint8)


def detect_annotated_rows(sat, rows, filled):
    """对每个**有内容**的行槽统计行窗内饱和像素数（x280-1000），返回标注行集合。
    空 footer 行槽不参与，避免页脚内容误判。"""
    hit = {}
    for i, y in enumerate(rows):
        if i < len(filled) and not filled[i]:
            hit[i] = 0
            continue
        c = int(sat[max(0, y - ROW_HALF):y + ROW_HALF, GRID_X0:GRID_X1].sum())
        hit[i] = c
    annotated = [i for i, c in hit.items() if c > ROW_ANNO_THRESH]
    return annotated, hit


def row_strips(img, rows, row_idx):
    """指定行 → 整行条（x280-1000, y±ROW_HALF），带原图 y 范围。"""
    out = []
    for i in row_idx:
        y = int(rows[i])
        y0, y1 = max(0, y - ROW_HALF), min(img.shape[0], y + ROW_HALF)
        out.append({"row": i, "row_y": y, "bbox": [GRID_X0, y0, GRID_X1, y1],
                    "image": img[y0:y1, GRID_X0:GRID_X1].copy()})
    return out


def build_row_stack(items, label_tmpl="row{row}"):
    """把 (row, image) 列表按行号顺序竖排成一张带行标签的栈图。
    单图输入 → 免多图 overhead、保留行间相对位置。返回缩放后的栈图。"""
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
        cv2.putText(stack, label_tmpl.format(row=row), (6, y + 26),
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
    stack = cv2.resize(stack, (nw, nh), interpolation=cv2.INTER_AREA)
    return stack


def column_sat_positions(strip_img, centers, x0=GRID_X0, half=60, thresh=30):
    """标注行条里，饱和色块覆盖了哪几个位置列（0=万..4=个）。
    centers 是原图绝对列心，x0 是行条左缘（条内列心=centers-x0）。"""
    sat = saturation_mask(strip_img)
    pos = []
    for i, cx in enumerate(centers):
        c = int(sat[:, cx - x0 - half: cx - x0 + half].sum())
        if c > thresh:
            pos.append(i)
    return pos


def draw_debug(img, annotated_strips):
    """在原图上画标注行裁剪框 + 网格区框，输出 debug 图。"""
    dbg = img.copy()
    cv2.rectangle(dbg, (GRID_X0, GRID_Y0), (GRID_X1, GRID_Y1), (0, 0, 0), 2)
    for s in annotated_strips:
        x0, y0, x1, y1 = s["bbox"]
        cv2.rectangle(dbg, (x0, y0), (x1, y1), (255, 0, 0), 3)
        cv2.putText(dbg, f"row{s['row']}", (x0 + 6, y0 + 28),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 0, 0), 2)
    return dbg


def process_image(path, geo):
    img = cv2.imread(path)
    if img is None:
        raise RuntimeError(f"读不到图片: {path}")
    sat = saturation_mask(img)
    rows = geo["rows"]
    filled = geo.get("rows_filled", [True] * len(rows))
    annotated, hit = detect_annotated_rows(sat, rows, filled)
    filled_idx = [i for i in range(len(rows)) if i < len(filled) and filled[i]]
    full_strips = row_strips(img, rows, filled_idx)
    anno_strips = row_strips(img, rows, annotated)
    full_stack = build_row_stack([(s["row"], s["image"]) for s in full_strips])
    anno_stack = build_row_stack([(s["row"], s["image"]) for s in anno_strips])
    info = {
        "image_size": list(img.shape[:2]),
        "grid_bbox": [GRID_X0, GRID_Y0, GRID_X1, GRID_Y1],
        "row_sat": {str(i): c for i, c in hit.items()},
        "annotated_rows": annotated,
        "n_annotated": len(annotated),
        "filled_rows": filled_idx,
        "annotated_strips": [{k: v for k, v in s.items() if k != "image"} for s in anno_strips],
        "saturated_positions": {
            str(s["row"]): column_sat_positions(s["image"], geo.get("column_centers", COLUMN_TEMPLATE))
            for s in anno_strips},
        "full_rows_size": list(full_stack.shape[:2]) if full_stack is not None else None,
        "annotated_size": list(anno_stack.shape[:2]) if anno_stack is not None else None,
    }
    return info, full_stack, anno_stack, full_strips, anno_strips


def main():
    fix_print()
    ap = argparse.ArgumentParser(description="Stage 2: 规律区域检测 + 裁剪")
    ap.add_argument("--manifest", required=True)
    args = ap.parse_args()

    manifest = load_json(args.manifest)
    if not manifest:
        print("[stage2] ERROR: 读不到 manifest", args.manifest)
        sys.exit(2)
    out_dir = manifest["out_dir"]
    geo_path = os.path.join(out_dir, "grid_geometry.json")
    geo_all = load_json(geo_path)
    if not geo_all:
        print(f"[stage2] ERROR: 读不到 {geo_path}（先跑 stage1）")
        sys.exit(2)

    crops_dir = os.path.join(out_dir, "crops")
    os.makedirs(crops_dir, exist_ok=True)
    result = {"run_id": manifest["run_id"], "blogger": manifest["blogger"],
              "target_period": manifest["target_period"],
              "target_draw": manifest["target_draw"], "images": {}}

    total_anno = 0
    for f in manifest["images"]:
        name = os.path.basename(f)
        stem = os.path.splitext(name)[0]
        geo = geo_all.get(name)
        if not geo:
            print(f"[stage2] 跳过（无几何）: {name}")
            continue
        info, full_stack, anno_stack, full_strips, anno_strips = process_image(f, geo)
        img_out = os.path.join(crops_dir, stem)
        os.makedirs(img_out, exist_ok=True)
        if full_stack is not None:
            cv2.imwrite(os.path.join(img_out, "01_rows.png"), full_stack)
        if anno_stack is not None:
            cv2.imwrite(os.path.join(img_out, "02_annotated.png"), anno_stack)
        dbg = draw_debug(cv2.imread(f), anno_strips)
        cv2.imwrite(os.path.join(img_out, "03_debug.png"), dbg)
        info["crops_dir"] = os.path.relpath(img_out, out_dir)
        info["full_rows_file"] = os.path.relpath(os.path.join(img_out, "01_rows.png"), out_dir)
        info["annotated_file"] = os.path.relpath(os.path.join(img_out, "02_annotated.png"), out_dir)
        info["debug_file"] = os.path.relpath(os.path.join(img_out, "03_debug.png"), out_dir)
        result["images"][name] = info
        total_anno += info["n_annotated"]
        print(f"[stage2] {name}: 标注行 {info['annotated_rows']} "
              f"全行栈{info['full_rows_size']} 标注栈{info['annotated_size']}")

    out_path = os.path.join(out_dir, "crops_manifest.json")
    write_json(result, out_path)
    print(f"[stage2] -> {out_path} (共 {total_anno} 个标注行条)")


if __name__ == "__main__":
    main()
