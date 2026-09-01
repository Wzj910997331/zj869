#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stage 0：输入准备 → manifest.json

- 解析博主名（归一化去私有区字符）、日期
- 发现图片：--images 显式指定，或按 posts.json 匹配博主 → 该帖 images 目录下 glob
- 读 lottery_recent.json，定位最新期（target_draw）
- 生成 run_id / out_dir，原子落盘 manifest.json
"""
import argparse
import glob
import os
import sys

from common import (REPO, IMAGES_BASE, RECOGNIZE_BASE, load_json, normalize_blogger,
                    write_json, fix_print)


def discover_images(blogger, date):
    """按博主名在 posts.json 里找帖 id → 图片目录 glob。"""
    date_key = date.replace("-", "")
    posts_path = os.path.join(REPO, "data", "crawl", date_key, "posts.json")
    posts = load_json(posts_path) or []
    target_id = None
    for p in posts:
        name = normalize_blogger((p.get("creator") or {}).get("name", ""))
        if name == blogger:
            target_id = p.get("id", "")
            break
    if not target_id:
        return []
    img_dir = IMAGES_BASE.format(date=date_key)
    # 帖 id 形如 s_2_d7c7ef9e-...，图片名 = <id>_<idx>.png
    prefix = os.path.join(img_dir, target_id)
    files = sorted(glob.glob(prefix + "_*.[pj][np][g]")) or sorted(glob.glob(prefix + ".*.[pj][np][g]"))
    return files


def main():
    fix_print()
    ap = argparse.ArgumentParser(description="Stage 0: 输入准备")
    ap.add_argument("--blogger", required=True, help="博主名，如 小屁股_483847515")
    ap.add_argument("--date", default="2026-08-28", help="采集日期 yyyy-mm-dd")
    ap.add_argument("--images", nargs="*", default=None, help="显式图片列表（覆盖自动发现）")
    ap.add_argument("--out", default=None, help="输出 manifest.json 路径（默认 data/recognize/...）")
    args = ap.parse_args()

    blogger = normalize_blogger(args.blogger)
    date_key = args.date.replace("-", "")

    if args.images:
        files = [os.path.abspath(p) for p in args.images]
    else:
        files = discover_images(blogger, args.date)
    files = [f for f in files if f.lower().endswith((".png", ".jpg", ".jpeg"))]
    if not files:
        print(f"[stage0] ERROR: 未找到 {blogger} 的走势图图片（date={args.date}）")
        sys.exit(2)
    # 校验文件存在
    missing = [f for f in files if not os.path.exists(f)]
    if missing:
        print(f"[stage0] ERROR: 图片不存在: {missing}")
        sys.exit(2)

    lottery_path = os.path.join(REPO, "data", "crawl", date_key, "lottery_recent.json")
    lottery = load_json(lottery_path)
    if not lottery:
        print(f"[stage0] ERROR: 读不到 {lottery_path}")
        sys.exit(2)
    # lottery_recent 最新在前（26230 为第一条）
    latest = lottery[0]
    target_period = str(latest.get("period", ""))
    target_draw = [int(x) for x in latest.get("numbers", [])]
    if len(target_draw) != 5:
        print(f"[stage0] ERROR: 最新期数字异常: {latest}")
        sys.exit(2)

    run_id = f"{date_key}_{target_period}"
    out_dir = os.path.join(RECOGNIZE_BASE, blogger, date_key)
    if args.out:
        out_dir = os.path.dirname(args.out)

    manifest = {
        "run_id": run_id,
        "blogger": blogger,
        "date": args.date,
        "date_key": date_key,
        "target_period": target_period,
        "target_draw": target_draw,
        "images": files,
        "n_images": len(files),
        "lottery_path": lottery_path,
        "n_lottery": len(lottery),
        "out_dir": out_dir,
        "stage": 0,
    }
    out_path = os.path.join(out_dir, "manifest.json")
    write_json(manifest, out_path)

    print(f"[stage0] OK: {blogger} / {args.date} / 目标期 {target_period}={target_draw}")
    print(f"[stage0]     图片 {len(files)} 张")
    for f in files:
        print(f"[stage0]       {os.path.basename(f)}")
    print(f"[stage0]     manifest -> {out_path}")


if __name__ == "__main__":
    main()
