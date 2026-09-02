#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_archive.py — 为 18 张 CONFIRM 图生成验证档案(供逐图人工/半自动分析)。

每张图输出: 底部 360px 渲染 ASCII(带颜色标记) + 全部标注 blob。
存到 /tmp/cv_verify_archive.txt。
"""
import sys
import os
import json
import glob

sys.path.insert(0, "/data/zhenjie/zj869/modules/image_recognize")
from cv_trend_reader.reader import load, render_ascii, detect_annotations  # noqa: E402

REPO = "/data/zhenjie/zj869"
IMG_ROOT = os.path.join(REPO, "data/crawl/260828")
VJSON = os.path.join(REPO, "data/crawl/20260828/verify_results_final7.json")

CM = {"red": ([0, 0, 150], [90, 90, 255]),
      "blue": ([150, 0, 0], [255, 110, 110]),
      "green": ([0, 150, 0], [110, 255, 110]),
      "yellow": ([0, 150, 150], [110, 255, 255]),
      "orange": ([0, 120, 200], [120, 200, 255]),
      "purple": ([120, 0, 120], [255, 110, 255])}


def main():
    d = json.load(open(VJSON))
    conf = [x for x in d["verdicts"] if x["verdict"] == "CONFIRM"]
    idx = {}
    for p in glob.glob(os.path.join(IMG_ROOT, "*", "*.*")):
        stem = os.path.basename(p).rsplit(".", 1)[0]
        idx.setdefault(stem, p)
    out = []
    seen = set()
    for x in conf:
        stem = x["file"].rsplit(".", 1)[0]
        if stem in seen:
            continue
        seen.add(stem)
        p = idx.get(stem)
        if not p:
            out.append(f"### MISSING {stem}")
            continue
        img = load(p)
        h, w = img.shape[:2]
        out.append(f"### {x['blogger']} | {os.path.basename(p)} | "
                   f"{w}x{h} | CONFIRM: {x['type']} {x['position']} {x['numbers']}")
        # 底部 360px 渲染
        y0 = max(0, h - 360)
        out.append(f"--- bottom y={y0}..{h} ---")
        out.append(render_ascii(img, bbox=(0, y0, w, h - y0), W=140, color_marks=CM))
        # 标注 blob
        ann = detect_annotations(img)
        for cname, items in ann.items():
            for t in items:
                cx, cy = t[0] + t[2] // 2, t[1] + t[3] // 2
                out.append(f"  {cname:6} kind={t[5]:4} box=({t[0]},{t[1]},{t[2]},{t[3]}) "
                           f"area={t[4]} center=({cx},{cy})")
        out.append("")
    txt = "\n".join(out)
    dst = "/tmp/cv_verify_archive.txt"
    with open(dst, "w", encoding="utf-8") as f:
        f.write(txt)
    print(f"写 {dst} ({len(txt)} 字符)")


if __name__ == "__main__":
    main()
