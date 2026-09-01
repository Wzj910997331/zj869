#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
crop_batch.py — 批量裁剪：某期全部图片按文件大小逆序 → 绿字走势图裁剪。

用于全量核验裁剪逻辑（如 26232 期 356 图）。仅 OpenCV（stage1 网格 + stage2 裁剪），无 LLM。
输出 data/recognize/<date>_by_size/{rank:03d}_{size}KB_{stem}/ + crops_manifest.json
每 25 张刷盘一次 _progress，崩溃可续。

用法：
  /usr/bin/python3 modules/image_recognize/crop_batch.py --date 20260830 [--limit N] [--min-green 0.003]
"""
import argparse
import glob
import os
import sys
import time

import cv2

import stage1_preprocess as s1
import stage2_crop as s2
from common import fix_print, write_json

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))


def main():
    fix_print()
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="采集目录名，如 20260830")
    ap.add_argument("--out", default=None, help="输出根目录（默认 data/recognize/<date>_by_size）")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 张（0=全部）")
    ap.add_argument("--min-green", type=float, default=0.003,
                    help="绿字像素占比低于此值判为非绿字走势图，跳过裁剪")
    args = ap.parse_args()

    img_dir = os.path.join(REPO, "data", "crawl", args.date, "images")
    files = sorted(f for f in glob.glob(os.path.join(img_dir, "*_*"))
                   if f.lower().endswith((".png", ".jpg", ".jpeg")))
    if not files:
        print(f"[crop] 无图片: {img_dir}")
        sys.exit(2)
    files.sort(key=lambda f: os.path.getsize(f), reverse=True)  # 按文件大小逆序
    if args.limit:
        files = files[:args.limit]
    out_root = args.out or os.path.join(REPO, "data", "recognize", f"{args.date}_by_size")
    os.makedirs(out_root, exist_ok=True)

    results = {"date": args.date, "sorted_by": "file_size_desc",
               "n_total": len(files), "min_green": args.min_green, "images": {}}
    t0 = time.time()
    done = cropped = skipped = failed = 0
    for rank, path in enumerate(files, 1):
        name = os.path.basename(path)
        sz = os.path.getsize(path)
        stem = os.path.splitext(name)[0]
        rec = {"file": name, "size_bytes": sz, "size_kb": sz // 1024}
        status = "?"
        try:
            img = cv2.imread(path)
            if img is None:
                rec["status"] = "unreadable"
                failed += 1
                status = "unreadable"
            else:
                rec["image_size"] = list(img.shape[:2])
                mg = s1.green_mask(img)
                rec["green_ratio"] = round(float(mg.mean()), 5)
                if rec["green_ratio"] < args.min_green:
                    rec["status"] = "no-green"     # 蓝字/彩色小图/截图，非绿字走势图
                    skipped += 1
                    status = "no-green"
                else:
                    geo = s1.process_image(path)
                    info, full_stack, anno_stack, full_strips, anno_strips = s2.process_image(path, geo)
                    d = os.path.join(out_root, f"{rank:03d}_{sz // 1024}KB_{stem}")
                    os.makedirs(d, exist_ok=True)
                    if full_stack is not None:
                        cv2.imwrite(os.path.join(d, "01_rows.png"), full_stack)
                    if anno_stack is not None:
                        cv2.imwrite(os.path.join(d, "02_annotated.png"), anno_stack)
                    cv2.imwrite(os.path.join(d, "03_debug.png"), s2.draw_debug(img, anno_strips))
                    rec.update({
                        "status": "cropped",
                        "annotated_rows": info["annotated_rows"],
                        "n_annotated": info["n_annotated"],
                        "saturated_positions": info["saturated_positions"],
                        "crop_dir": os.path.relpath(d, out_root),
                    })
                    cropped += 1
                    status = "cropped"
        except Exception as e:
            msg = str(e)[:200]
            # 绿字非行网格版式（杀号表/窄条绿/散点绿）不是可裁剪走势图，独立归类
            if "未检出数字行带" in msg:
                rec["status"] = "no-grid"
                skipped += 1
            else:
                rec["status"] = "error"
                rec["error"] = msg
                failed += 1
            status = rec["status"]
        results["images"][name] = rec
        done += 1
        if done % 25 == 0:
            results["_progress"] = {"done": done, "total": len(files), "cropped": cropped,
                                    "skipped": skipped, "failed": failed,
                                    "elapsed_s": round(time.time() - t0, 1)}
            write_json(results, os.path.join(out_root, "crops_manifest.json"))
        print(f"[crop] {done}/{len(files)} rank{rank:03d} {name} "
              f"{rec.get('image_size', '?')} {rec['size_kb']}KB -> {status} "
              f"标注{rec.get('n_annotated', '')}", flush=True)

    results["_progress"] = {"done": done, "total": len(files), "cropped": cropped,
                            "skipped": skipped, "failed": failed,
                            "elapsed_s": round(time.time() - t0, 1)}
    write_json(results, os.path.join(out_root, "crops_manifest.json"))
    print(f"[crop] DONE {done}/{len(files)} cropped={cropped} "
          f"no-green={skipped} failed={failed} {time.time() - t0:.0f}s", flush=True)
    print(f"[crop] -> {os.path.join(out_root, 'crops_manifest.json')}", flush=True)


if __name__ == "__main__":
    main()
