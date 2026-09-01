#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
串行分批识别驱动 v3：每轮重算"排序序第一个未完成文件"作为 offset。
通过 subprocess 前台子进程通道调用（已验证该通道网络正常）。
每批 BATCH 张；子进程超时则跳过该批重试；直到期完成。
用法: 用 run_in_background 启动（本驱动内部 spawn 前台子进程）。
"""
import json
import os
import re
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = "/data/zhenjie/zj869"
BATCH = 10
SUBTIMEOUT = 900  # 每批子进程最长 15 分钟（图片按面积排序越后越大，识别越慢，需同步放大）
MAX_CONSECUTIVE_NO_PROGRESS = 6  # 连续多轮零新增→ 等待 60s

# 原图白名单：s_2_<uuid>_<n>.<ext>。其他管线（image_recognize stage4_direct 等）
# 会在 images 目录旁写 *.direct.jpg / *.loc.jpg 等临时缩放图，必须排除，
# 否则会被当成未识别新图重复识别、污染结果。
SOURCE_RE = re.compile(r"^s_2_[0-9a-f-]{36}_\d+\.(jpg|jpeg|png)$")

PERIODS = [
    {"base": "20260829", "period": "26231", "calib": "26230", "calib_draw": "9 4 6 8 3"},
    {"base": "20260830", "period": "26232", "calib": "26231", "calib_draw": "1 8 7 9 9"},
]


def image_area(path):
    try:
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
            return w * h
    except Exception:
        return os.path.getsize(path)


def sorted_files(base):
    d = os.path.join(REPO, "data", "crawl", base, "images")
    files = [f for f in os.listdir(d) if SOURCE_RE.match(f)]
    files.sort(key=lambda f: image_area(os.path.join(d, f)))
    return files


def existing_set(base):
    p = os.path.join(REPO, "data", "crawl", base, "vision_patterns_full.json")
    try:
        return {v["file"] for v in json.load(open(p, encoding="utf-8"))}
    except Exception:
        return set()


def first_undone(base):
    files = sorted_files(base)
    ex = existing_set(base)
    i = 0
    while i < len(files) and files[i] in ex:
        i += 1
    return i, len(files)


def dynamic_batch(base, offset):
    """大图识别更慢：按批首图面积缩小每批张数，让一批能在 SUBTIMEOUT 内跑完。
    超时估算与 recognize.adaptive_timeout 一致（max(60, area//60000)，封顶 180s）。"""
    d = os.path.join(REPO, "data", "crawl", base, "images")
    files = sorted_files(base)
    if offset >= len(files):
        return BATCH
    a = image_area(os.path.join(d, files[offset]))
    to = min(180, max(60, a // 60000))
    return max(3, min(BATCH, int(SUBTIMEOUT * 0.8 / to)))


def run_batch(base, period, calib, calib_draw, out, offset, batch):
    cmd = [sys.executable, "-u", os.path.join(REPO, "tools", "recognize_patterns.py"),
           "--base", os.path.join("data", "crawl", base), "--period", period,
           "--calib", calib, "--calib-draw", calib_draw,
           "--workers", "1", "--offset", str(offset), "--limit", str(batch),
           "--out", out, "--resume"]
    try:
        r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=SUBTIMEOUT)
        return r.returncode, (r.stdout or ""), (r.stderr or "")
    except subprocess.TimeoutExpired:
        # 子进程超时：不崩溃，返回 rc=124（走重试分支）；子进程已被 subprocess 杀掉
        return 124, "", "subprocess timeout"


def main():
    print(f"═══ 分批识别驱动v3 启动 {time.strftime('%H:%M:%S')}（每批{BATCH}张，first_undone 推进）═══", flush=True)
    for p in PERIODS:
        base, period = p["base"], p["period"]
        out = os.path.join("data", "crawl", base, "vision_patterns_full.json")
        offset, total = first_undone(base)
        n_done = offset
        print(f"▶ 期 {period}（{base}）已 {offset}/{total}", flush=True)
        stagnant = 0
        while offset < total:
            batch = dynamic_batch(base, offset)
            rc, out_s, err_s = run_batch(base, period, p["calib"], p["calib_draw"], out, offset, batch)
            new_offset, _ = first_undone(base)
            added = new_offset - offset
            if rc == 0 and added > 0:
                offset = new_offset
                stagnant = 0
                print(f"  ✓ 批offset={new_offset - added} 新增{added} → 总{offset}/{total}（{offset/total:.0%}）", flush=True)
            elif rc == 0:
                stagnant += 1
                if stagnant >= MAX_CONSECUTIVE_NO_PROGRESS:
                    print(f"  ⚠ 连续{stagnant}轮零新增（offset={offset}）→ 等待60s", flush=True)
                    time.sleep(60)
                    stagnant = 0
                else:
                    print(f"  · 批零新增（offset={offset}）重试", flush=True)
                    time.sleep(15)
            else:
                # 子进程异常/超时：若已有部分进度落盘则推进 offset，等待后继续
                if added > 0:
                    offset = new_offset
                    stagnant = 0
                    print(f"  △ 批rc={rc}但已保存{added}张 → offset推进到{offset}，等待60s继续", flush=True)
                    time.sleep(60)
                else:
                    print(f"  ✗ 批rc={rc}（零进度）→ 慢窗口等待60s重试", flush=True)
                    time.sleep(60)
        print(f"✅ 期 {period} 完成：{offset}/{total}", flush=True)

    print(f"═══ 两期识别全部完成 {time.strftime('%H:%M:%S')} ═══", flush=True)


if __name__ == "__main__":
    main()
