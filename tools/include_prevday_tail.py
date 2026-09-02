#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""include_prevday_tail.py — 把「前一天开奖后(≥前日开奖时刻)发帖」并入当期池并重导出。

背景（2026-09-03 用户拍板）：博主常在**前一天 21:30(上一期开奖)后、当晚 24 点前**就发
下一期的预测。爬虫按自然日存目录 → 这些帖子落在 <前一日> 目录里，跑 <开奖日> 池时被漏掉
（上一期把它们当"复盘 ≥21:30"剔了）。本工具把这些晚发帖重新以目标期 P 走 ①→③ 判定，
并入 <开奖日> 主池，再 ④ verify → ⑤ export，覆盖 docs/规律/<period>.{json,md}。

用法（在主池 ①–⑤ 已跑完后调用）：
  python3 tools/include_prevday_tail.py \
      --period 26233 --draw "1 6 3 4 0" \
      --calib-period 26232 --calib-draw "8 0 2 3 3" \
      --main-date 20260831 --prev-date 20260830 \
      --prev-cutoff "2026-08-30 21:30" \
      --lottery data/crawl/20260831/lottery_recent.json

流程：
  1. 选 <prev-date> 目录里 create_time ≥ --prev-cutoff 的帖子图片 → symlink 到 <tail_dir>/images
  2. filter_trend (target=P)  → ③ blogger_hit_gate → extract_prediction_strip → read_blogger_prediction
     （各步若产物已存在则复用，幂等）
  3. 合并 <main-date>/blogger_predictions.json + <tail_dir>/blogger_predictions.json
  4. verify_blogger_prediction(合并) → export_blogger_prediction → 覆盖 docs/规律/<period>.{json,md}
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_PAT = re.compile(r"s_2_[0-9a-f-]+_\d+\.(?:jpg|jpeg|png)$")
UUID_PAT = re.compile(r"(s_2_[0-9a-f-]+)_\d+\.(?:jpg|jpeg|png)$")


def read_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def write_json(o, p):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(o, f, ensure_ascii=False, indent=1)


def run(script, *args):
    cmd = [sys.executable, os.path.join(REPO, script), *args]
    print(f"\n$ {' '.join(cmd)}", flush=True)
    r = subprocess.run(cmd, capture_output=False)
    if r.returncode != 0:
        sys.exit(f"  ✗ {script} 退出码 {r.returncode}")
    return r


def build_tail_images(prev_date, prev_cutoff, tail_dir):
    """把 prev_date 目录里发帖时间 ≥ prev_cutoff 的图片 symlink 成 tail 池。返回文件数。"""
    prev = os.path.join(REPO, "data", "crawl", prev_date)
    posts = read_json(os.path.join(prev, "posts.json"))
    byid = {p["id"]: p.get("create_time", "") for p in posts if p.get("id")}
    img_dir = os.path.join(prev, "images")
    files = [f for f in sorted(os.listdir(img_dir)) if SRC_PAT.match(f)]

    def tm(f):
        m = UUID_PAT.match(f)
        return byid.get(m.group(1), "") if m else ""

    tail = [f for f in files if tm(f) >= prev_cutoff]
    os.makedirs(os.path.join(tail_dir, "images"), exist_ok=True)
    n_new = 0
    for f in tail:
        dst = os.path.join(tail_dir, "images", f)
        if not os.path.lexists(dst):
            os.symlink(os.path.join(img_dir, f), dst)
            n_new += 1
    print(f"[tail] {prev_date} 帖子 {len(files)} 张图；≥{prev_cutoff} → {len(tail)} 张进尾池"
          f"（新 symlink {n_new}）")
    return tail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", required=True)
    ap.add_argument("--draw", required=True)
    ap.add_argument("--calib-period", default="")
    ap.add_argument("--calib-draw", default="")
    ap.add_argument("--main-date", required=True, help="开奖日 crawl 目录，如 20260831")
    ap.add_argument("--prev-date", required=True, help="前一天 crawl 目录，如 20260830")
    ap.add_argument("--prev-cutoff", default="21:30",
                    help="前一天发帖起点：'21:30'(同日比时分) 或 '2026-08-30 21:30'(跨天比完整时间)。"
                         "建议给完整时间(上期开奖时刻)")
    ap.add_argument("--tail-dir", default=None,
                    help="尾池目录(默认 data/crawl/<prev-date>_tail)")
    ap.add_argument("--lottery", default=None,
                    help="开奖表(默认 data/crawl/<main-date>/lottery_recent.json)")
    args = ap.parse_args()

    main_dir = os.path.join(REPO, "data", "crawl", args.main_date)
    tail_dir = args.tail_dir or os.path.join(REPO, "data", "crawl", f"{args.prev_date}_tail")
    lottery = args.lottery or os.path.join(main_dir, "lottery_recent.json")
    if not os.path.isdir(main_dir):
        sys.exit(f"✗ 主池目录不存在: {main_dir}")

    # ---- 1. 尾池（幂等：产物已存在则复用）----
    build_tail_images(args.prev_date, args.prev_cutoff, tail_dir)

    fr = os.path.join(tail_dir, "filter_report.json")
    gate = os.path.join(tail_dir, "blogger_hit_gate.json")
    man = os.path.join(tail_dir, "strips", "manifest.json")
    preds = os.path.join(tail_dir, "blogger_predictions.json")
    imgs_dir = os.path.join(tail_dir, "images")

    if not os.path.exists(fr):
        run("modules/image_recognize/filter_trend.py",
            "--date", os.path.basename(tail_dir), "--target-period", args.period,
            "--lottery", lottery, "--window", "5", "--out", fr)
    if not os.path.exists(gate):
        run("modules/image_recognize/blogger_hit_gate.py",
            "--date", os.path.basename(tail_dir), "--filter", fr, "--images", imgs_dir,
            "--target-period", args.period, "--calib-period", args.calib_period,
            "--calib-draw", args.calib_draw, "--out", gate)
    if not os.path.exists(man):
        run("modules/image_recognize/extract_prediction_strip.py",
            "--date", os.path.basename(tail_dir), "--filter", fr, "--gate", gate,
            "--images", imgs_dir, "--target-period", args.period,
            "--calib-period", args.calib_period, "--out", os.path.join(tail_dir, "strips"))
    if not os.path.exists(preds):
        run("modules/image_recognize/read_blogger_prediction.py",
            "--date", os.path.basename(tail_dir), "--period", args.period, "--draw", args.draw,
            "--calib", args.calib_period, "--calib-draw", args.calib_draw,
            "--strips", os.path.join(tail_dir, "strips"),
            "--posts", os.path.join(REPO, "data", "crawl", args.prev_date, "posts.json"),
            "--out", preds, "--batch", "8", "--workers", "3", "--model", "auto",
            "--cutoff", f"{args.main_date[:4]}-{args.main_date[4:6]}-{args.main_date[6:8]} 21:30")

    # ---- 2. 合并主池 + 尾池 predictions ----
    main_pred = os.path.join(main_dir, "blogger_predictions.json")
    if not os.path.exists(main_pred):
        sys.exit(f"✗ 主池 predictions 不存在(先跑主池 ①–⑤): {main_pred}")
    mp = read_json(main_pred)
    tp = read_json(preds)
    merged = dict(mp)
    merged["predictions"] = list(mp.get("predictions", [])) + list(tp.get("predictions", []))
    merged["n_strips"] = mp.get("n_strips", len(mp.get("predictions", []))) + \
        tp.get("n_strips", len(tp.get("predictions", [])))
    merged["说明"] = f"{mp.get('说明','')}；[补]并入前一晚(≥{args.prev_cutoff})发帖 {len(tp.get('predictions', []))} 条"
    merged_pred = os.path.join(main_dir, "blogger_predictions_merged.json")
    write_json(merged, merged_pred)
    print(f"[merge] 主池 {len(mp.get('predictions',[]))} + 尾池 {len(tp.get('predictions',[]))} "
          f"= {len(merged['predictions'])} → {merged_pred}")

    # ---- 3. verify(合并) → export → 覆盖 docs ----
    merged_verify = os.path.join(main_dir, "blogger_predictions_verify_merged.json")
    run("modules/image_recognize/verify_blogger_prediction.py",
        "--date", args.main_date, "--period", args.period, "--draw", args.draw,
        "--pred", merged_pred, "--out", merged_verify)
    run("tools/export_blogger_prediction.py",
        "--verify", merged_verify, "--period", args.period, "--draw", args.draw)
    print("\n✅ 合并导出完成：docs/规律/{}.{{json,md}} 已覆盖（含前一晚尾池）".format(args.period))


if __name__ == "__main__":
    main()
