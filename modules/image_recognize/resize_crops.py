#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
resize_crops.py — ③ 显式 resize 步骤(流程改造第三步)。

用户意图(2026-09-01): 裁剪产物(02_annotated.png, 640 宽栈图)不直接当视觉输入,
需一道独立 resize 步骤统一到视觉友好尺寸/格式, 便于视觉模型读取稳定、负载小。

规则(夹逼到视觉友好尺寸):
  scale = min(1.0, max_w/w, max_h/h); 仅 scale<1 时 cv2.resize(INTER_AREA 下采样);
  过小(<min_w 宽, 如 crop_all 栈图固定 640)则 INTER_CUBIC 上采样到 ≥min_w 宽——
  浅色数字在 640 宽下视觉模型读不清(冒烟实测死循环), 放大后 33s 可读。
  统一 JPEG q=90 编码。中文路径安全(np.fromfile/imdecode + imencode/tofile)。

输入: filter_report.json 的 keep/uncertain 图 + crops_all_manifest.json(crop_dir → 02_annotated.png)
输出(新文件, 不覆盖任何现有产物):
  <out_dir>/{stem}.jpg                   每张 keep/uncertain 裁剪图的 resize 结果
  <out_dir>/../vision_manifest.json      {file → {src_crop, resized_path, size_before, size_after}}

用法:
  /usr/bin/python3 modules/image_recognize/resize_crops.py \
    --date 20260831 \
    --filter data/crawl/20260831/filter_report.json \
    --manifest data/recognize/20260831_all/crops_all_manifest.json \
    --out-dir data/recognize/20260831_all/vision [--max-w 1024 --max-h 2200 --min-w 1024 --jpeg-q 90] [--limit N]
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from common import REPO, load_json, write_json, fix_print  # noqa: E402
from cv_trend_reader.reader import load  # noqa: E402


def keep_or_uncertain(decision):
    """v2/v3 决策名: keep-high / keep-med / uncertain(period-weak|anno-weak)。
    keep* / uncertain* 均进入下游(裁剪→resize→视觉)。"""
    return decision.startswith("keep") or decision.startswith("uncertain")


def resize_crop(src, dst, max_w=1024, max_h=2200, q=90, min_w=1024):
    """读裁剪图 → 统一到视觉友好尺寸 → JPEG 编码落盘。返回 (before_w, before_h, after_w, after_h)。

    2026-09-01 冒烟实测: crop_all 栈图固定 640 宽,浅色数字经 resize 再降采样后
    视觉模型读不清(死循环超时)。故改为"夹逼"到 [min_w, max_w]: 过小则 INTER_CUBIC
    上采样到 ≥min_w(保行标签/数字可读), 过大则 INTER_AREA 下采样到 ≤max_w/max_h。
    """
    img = load(src)
    if img is None:
        raise ValueError(f"无法解码: {src}")
    h, w = img.shape[:2]
    scale = min(1.0, max_w / w, max_h / h)
    if scale < 1.0:                      # 过大 → 下采样
        nh, nw = int(round(h * scale)), int(round(w * scale))
        out = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
    elif w < min_w:                      # 过小 → 上采样到 min_w 宽
        scale = min(min_w / w, max_h / h)
        nh, nw = int(round(h * scale)), int(round(w * scale))
        out = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_CUBIC)
    else:
        out = img
    ok, buf = cv2.imencode(".jpg", out, [int(cv2.IMWRITE_JPEG_QUALITY), q])
    if not ok:
        raise ValueError(f"编码失败: {dst}")
    buf.tofile(dst)
    return (w, h, out.shape[1], out.shape[0])


def main():
    fix_print()
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--filter", required=True, help="filter_report.json 路径")
    ap.add_argument("--manifest", required=True, help="crops_all_manifest.json 路径")
    ap.add_argument("--out-dir", required=True, help="resize 产物目录(如 data/recognize/{date}_all/vision)")
    ap.add_argument("--max-w", type=int, default=1024)
    ap.add_argument("--max-h", type=int, default=2200)
    ap.add_argument("--min-w", type=int, default=1024)
    ap.add_argument("--jpeg-q", type=int, default=90)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--files", default=None, help="逗号分隔指定源图文件名(冒烟用, 跳过 keep/uncertain 序)")
    args = ap.parse_args()

    fr = load_json(args.filter) or {}
    manifest = load_json(args.manifest) or {}
    if not fr.get("images") or not manifest.get("images"):
        print("[resize] ERROR: 读不到 filter_report 或 crops_all_manifest")
        sys.exit(2)

    imgs = manifest["images"]
    crops_root = os.path.dirname(os.path.abspath(args.manifest))
    os.makedirs(args.out_dir, exist_ok=True)

    todo = [f for f, r in fr.get("images", {}).items()
            if keep_or_uncertain(r.get("decision")) and imgs.get(f, {}).get("status") == "cropped"]
    if args.files:
        want = {f.strip() for f in args.files.split(",") if f.strip()}
        todo = [f for f in todo if f in want]
    elif args.limit:
        todo = todo[:args.limit]
    if not todo:
        print("[resize] 无可 resize 的 keep/uncertain 图")

    results, missing = {}, []
    for f in todo:
        crop_dir = imgs[f].get("crop_dir", "")
        src = os.path.join(crops_root, crop_dir, "02_annotated.png")
        stem = os.path.splitext(f)[0]
        dst = os.path.join(args.out_dir, f"{stem}.jpg")
        if not os.path.exists(src):
            missing.append(f)
            continue
        try:
            bw, bh, aw, ah = resize_crop(src, dst, args.max_w, args.max_h, args.jpeg_q, args.min_w)
            results[f] = {"src_crop": src, "resized_path": dst,
                          "size_before": [bw, bh], "size_after": [aw, ah]}
        except Exception as e:
            results[f] = {"error": str(e)[:150]}

    vision_manifest_path = os.path.join(os.path.dirname(args.out_dir.rstrip("/")), "vision_manifest.json")
    write_json({"date": args.date, "generated_by": "resize_crops.py",
                "n_resized": len(results), "images": results}, vision_manifest_path)
    print(f"[resize] DONE {len(results)}/{len(todo)} 张 -> {args.out_dir}")
    if missing:
        print(f"[resize] 缺裁剪图 {len(missing)}: {missing[:5]}")
    print(f"[resize] -> {vision_manifest_path}")


if __name__ == "__main__":
    main()
