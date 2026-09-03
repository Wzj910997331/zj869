#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""include_prevday_tail.py — 期 P 博主单押 ①–⑤ **默认运行器**（自动并入前一天尾池，2026-09-03 改造）。

背景（用户拍板「默认就要考虑前一天的尾迟，不用我主动加」）：
  博主常在**前一天 21:30(上期开奖)后、当晚 24 点前**就发下一期 P 的预测。爬虫按自然日存目录
  → 这些帖子落在 <前一日> 目录里，若只跑 <开奖日> 池会被漏掉（上一期把它当复盘剔了）。
  本工具把「前一日 ≥ 前日开奖时刻」的晚发帖，自动以目标期 P 重走 ①→③，与 <开奖日> 主池
  的 predictions 合并 → ④ verify → ⑤ export，覆盖 docs/规律/<period>.{json,md}。
  也就是：**跑一个期 = 跑本脚本一条命令**，尾池默认并入，无需额外步骤。

  2026-09-03 再加固：
    - 前一日目录缺失时**自动 crawl_gouli 补爬**（爬失败仅跳过尾池，重跑同命令即并入）。
    - **期号备注 × 时间切期交叉核验**：predictions 里正文唯一显式期号 ≠ 目标期的条目，
      以期号为准剔出（博主自标别的期，比时间窗口更具体）；不误剔无/多期歧义，不拉回复盘帖。

用法（期 P 一键 ①–⑤，主池产物缺失会自动先跑主池，幂等可重复执行）：
  python3 tools/include_prevday_tail.py \
      --period 26233 --draw "1 6 3 4 0" \
      --calib-period 26232 --calib-draw "8 0 2 3 3" \
      --main-date 20260831 \
      --lottery data/crawl/20260831/lottery_recent.json

  可选：
    --prev-date  20260830         # 默认 = main_date 前一天
    --prev-cutoff "2026-08-30 22:00"   # 默认 = 前一日 21:30(上期开奖时刻)；短形式 "22:00" 也会自动补前一日日期
    --no-tail                      # 只要主池（跳过尾池并入）
    --tail-dir data/crawl/20260830_tail   # 尾池目录，默认 data/crawl/<prev-date>_tail

流程（每步产物已存在则复用 → 幂等；不会重复烧视觉）：
  主池:  filter_trend(target=P) → blogger_hit_gate → extract_prediction_strip
        → read_blogger_prediction(--model auto, cutoff=开奖日 21:30)
  尾池:  选 <prev-date> 里 create_time ≥ prev_cutoff 的图 → symlink 尾池
        → 同上 ①→③（target=P, cutoff=开奖日 21:30 确保无复盘残留）
  合并:  主池 + 尾池 predictions → blogger_predictions_merged.json
  ④⑤:   verify(合并) → export → 覆盖 docs/规律/<period>.{json,md}
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_PAT = re.compile(r"s_2_[0-9a-f-]+_\d+\.(?:jpg|jpeg|png)$")
UUID_PAT = re.compile(r"(s_2_[0-9a-f-]+)_\d+\.(?:jpg|jpeg|png)$")
PERIOD_NOTE_RE = re.compile(r"(?<!\d)(\d{5})(?!\d)\s*期")  # 与 tools/crawl_gouli.py 保持一致


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


def full_dt(ymd, hm):
    """'20260831' + '21:30' → '2026-08-31 21:30'（跨天目录比较须用完整时间）"""
    return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]} {hm}"


def build_tail_images(prev_date, prev_cutoff, tail_dir):
    """把 prev_date 目录里发帖时间 ≥ prev_cutoff 的图片 symlink 成 tail 池。返回命中文件数。"""
    prev = os.path.join(REPO, "data", "crawl", prev_date)
    if not os.path.isdir(prev):
        print(f"[tail] ⚠ 前一日目录不存在，跳过尾池: {prev}")
        return 0
    posts_p = os.path.join(prev, "posts.json")
    img_dir = os.path.join(prev, "images")
    if not (os.path.exists(posts_p) and os.path.isdir(img_dir)):
        print(f"[tail] ⚠ {prev} 缺 posts.json/images，跳过尾池")
        return 0
    posts = read_json(posts_p)
    byid = {p["id"]: p.get("create_time", "") for p in posts if p.get("id")}
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
    return len(tail)


def explicit_period(content):
    """正文唯一显式期号 → 5 位字符串；无 / 多个不同期号（如《4020》一串复盘期号）→ None。"""
    if not content:
        return None
    s = set(m.group(1) for m in PERIOD_NOTE_RE.finditer(str(content)))
    return s.pop() if len(s) == 1 else None


def crawl_prev_day(prev_date):
    """前一日目录缺失时自动 crawl_gouli 补爬（网络失败返回 False，调用侧跳过尾池）。"""
    iso = f"{prev_date[:4]}-{prev_date[4:6]}-{prev_date[6:8]}"
    cmd = [sys.executable, os.path.join(REPO, "tools", "crawl_gouli.py"), iso]
    print(f"[tail] 前一日目录缺失 → 自动爬取 {iso}")
    r = subprocess.run(cmd)
    return r.returncode == 0


def period_note_crosscheck(pred_path, posts_path, period, pool_label):
    """期号备注 × 时间切期 交叉核验：predictions 里「正文唯一显式期号 ≠ 目标期」的条目，
    以期号备注为准剔出（正文比时间窗口更具体：博主自标的就是另一期）；无期号/多期歧义
    不误剔。只做负向收窄——绝不把开奖后复盘帖（已被 --cutoff 剔）拉回来。
    幂等：剔完重跑不再命中。"""
    if not pred_path or not os.path.exists(pred_path) or not os.path.exists(posts_path):
        return
    byid = {p.get("id"): p.get("content", "") for p in read_json(posts_path) if p.get("id")}
    d = read_json(pred_path)
    items = d.get("predictions") or []
    keep, dropped = [], []
    for r in items:
        m = UUID_PAT.match(r.get("file") or "")
        e = explicit_period(byid.get(m.group(1), "")) if m else None
        if e is not None and e != str(period):
            dropped.append({"blogger": r.get("blogger"), "file": r.get("file"),
                            "period_in_text": e})
        else:
            keep.append(r)
    if not dropped:
        return
    d["predictions"] = keep
    d["n_strips"] = len(keep)
    d["n_periodnote_dropped"] = len(dropped)
    d["periodnote_dropped"] = dropped
    d["说明"] = (f"{d.get('说明', '')}；[期号备注] 正文显式标 {period} 之外期号的 "
                f"{len(dropped)} 条以期号为准剔除")
    write_json(d, pred_path)
    print(f"[期号备注] {pool_label}: 正文显式期号≠{period} 剔除 {len(dropped)} 条 "
          f"({[x['period_in_text'] for x in dropped]})")


def ensure_pool(date_dir, period, draw, calib_period, calib_draw, lottery, pool_label,
                cutoff_full, posts_p=None):
    """让一个池子跑完 ① filter → ② gate → ③ strip → ③ read。产物在就复用。返回 predictions 路径。"""
    fr = os.path.join(date_dir, "filter_report.json")
    gate = os.path.join(date_dir, "blogger_hit_gate.json")
    strips = os.path.join(date_dir, "strips")
    man = os.path.join(strips, "manifest.json")
    preds = os.path.join(date_dir, "blogger_predictions.json")
    base = os.path.basename(date_dir)
    imgs = os.path.join(date_dir, "images")
    posts_p = posts_p or os.path.join(date_dir, "posts.json")

    # filter_trend v5 会自写全 keep 的行窄条 + manifest；博主单押链只消费
    # gate=pass 的 cols 窄条（extract_prediction_strip 产物，见下）。故把 v5 的
    # 自产窄条导向独立暂存目录，避免覆盖/混入消费目录 data/crawl/<date>/strips。
    filter_strips = os.path.join(date_dir, ".filter_v5_strips")

    if not os.path.exists(fr):
        run("modules/image_recognize/filter_trend.py",
            "--date", base, "--target-period", period,
            "--lottery", lottery, "--workers", "8",
            "--out-dir", filter_strips, "--out", fr)
    if not os.path.exists(gate):
        run("modules/image_recognize/blogger_hit_gate.py",
            "--date", base, "--filter", fr, "--images", imgs,
            "--target-period", period, "--calib-period", calib_period,
            "--calib-draw", calib_draw, "--out", gate)
    # 消费目录 strips/manifest 只认 extract 产物（gate=pass 子集）：
    # 缺失、或残留 v5 自写的全 keep manifest 时都必须重跑 extract 收敛。
    man_is_extract = (os.path.exists(man) and
                      read_json(man).get("generated_by") == "extract_prediction_strip.py")
    if not man_is_extract:
        run("modules/image_recognize/extract_prediction_strip.py",
            "--date", base, "--filter", fr, "--gate", gate,
            "--images", imgs, "--target-period", period,
            "--calib-period", calib_period, "--out", strips)
    if not os.path.exists(preds):
        run("modules/image_recognize/read_blogger_prediction.py",
            "--date", base, "--period", period, "--draw", draw,
            "--calib", calib_period, "--calib-draw", calib_draw,
            "--strips", strips,
            "--posts", posts_p,
            "--out", preds, "--batch", "8", "--workers", "3",
            "--model", "auto", "--cutoff", cutoff_full)
    else:
        print(f"[复用] {pool_label} blogger_predictions.json 已存在 → {preds}")
    return preds


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", required=True)
    ap.add_argument("--draw", required=True)
    ap.add_argument("--calib-period", default="")
    ap.add_argument("--calib-draw", default="")
    ap.add_argument("--main-date", required=True, help="开奖日 crawl 目录，如 20260831")
    ap.add_argument("--prev-date", default=None,
                    help="前一天 crawl 目录(默认 main_date 前一天，如 20260830)")
    ap.add_argument("--prev-cutoff", default=None,
                    help="前一日发帖起点：'21:30'(自动补前一日日期) 或完整 '2026-08-30 21:30'。"
                         "默认 = 前一日 21:30(上期开奖时刻)")
    ap.add_argument("--no-tail", action="store_true", help="只要主池，不并入前一天尾池")
    ap.add_argument("--tail-dir", default=None,
                    help="尾池目录(默认 data/crawl/<prev-date>_tail)")
    ap.add_argument("--lottery", default=None,
                    help="开奖表(默认 data/crawl/<main-date>/lottery_recent.json)")
    args = ap.parse_args()

    main_dir = os.path.join(REPO, "data", "crawl", args.main_date)
    if not os.path.isdir(main_dir):
        sys.exit(f"✗ 主池目录不存在: {main_dir}")
    lottery = args.lottery or os.path.join(main_dir, "lottery_recent.json")
    main_cut = full_dt(args.main_date, "21:30")
    prev_date = args.prev_date or (datetime.strptime(args.main_date, "%Y%m%d")
                                   - timedelta(days=1)).strftime("%Y%m%d")
    if args.prev_cutoff:
        prev_cut = (args.prev_cutoff if "-" in args.prev_cutoff
                    else full_dt(prev_date, args.prev_cutoff))
    else:
        prev_cut = full_dt(prev_date, "21:30")

    # ---- 1. 主池 ①-③（产物已存在则复用）----
    main_pred = ensure_pool(main_dir, args.period, args.draw,
                            args.calib_period, args.calib_draw, lottery,
                            "主池", main_cut)

    # ---- 2. 尾池 ①-③（默认并入前一天 ≥前日开奖 的晚发帖；目录缺失自动补爬）----
    tail_pred = None
    if not args.no_tail:
        prev_dir = os.path.join(REPO, "data", "crawl", prev_date)
        if not os.path.isdir(prev_dir) and not crawl_prev_day(prev_date):
            print("[tail] ⚠ 自动爬取前一日失败 → 本次只跑主池；网络恢复后重跑同一条命令即并入尾池")
        if os.path.isdir(prev_dir):
            tail_dir = args.tail_dir or os.path.join(REPO, "data", "crawl", f"{prev_date}_tail")
            n = build_tail_images(prev_date, prev_cut, tail_dir)
            if n > 0:
                prev_posts = os.path.join(prev_dir, "posts.json")
                tail_pred = ensure_pool(tail_dir, args.period, args.draw,
                                        args.calib_period, args.calib_draw, lottery,
                                        "尾池", main_cut, posts_p=prev_posts)
            else:
                print("[tail] 无 ≥ 截止的晚发帖，跳过尾池读取")
    else:
        print("[tail] --no-tail，跳过前一天尾池")

    # ---- 2.5 期号备注 × 时间切期 交叉核验（以期号为准做负向剔除，见 period_note_crosscheck）----
    period_note_crosscheck(main_pred, os.path.join(main_dir, "posts.json"),
                           args.period, "主池")
    if tail_pred:
        period_note_crosscheck(tail_pred,
                               os.path.join(REPO, "data", "crawl", prev_date, "posts.json"),
                               args.period, "尾池")

    # ---- 3. 合并主池 + 尾池 predictions ----
    mp = read_json(main_pred)
    tp = read_json(tail_pred) if tail_pred else None
    merged = dict(mp)
    merged["predictions"] = list(mp.get("predictions", []))
    n_tail = 0
    if tp:
        merged["predictions"] += list(tp.get("predictions", []))
        n_tail = len(tp.get("predictions", []))
    merged["n_strips"] = mp.get("n_strips", len(mp.get("predictions", []))) + n_tail
    merged["说明"] = f"{mp.get('说明','')}；[并入]前一日(≥{prev_cut})晚发帖 {n_tail} 条"
    merged_pred = os.path.join(main_dir, "blogger_predictions_merged.json")
    write_json(merged, merged_pred)
    print(f"[merge] 主池 {len(mp.get('predictions',[]))} + 尾池 {n_tail} "
          f"= {len(merged['predictions'])} → {merged_pred}")

    # ---- 4. verify(合并) → export → 覆盖 docs ----
    merged_verify = os.path.join(main_dir, "blogger_predictions_verify_merged.json")
    run("modules/image_recognize/verify_blogger_prediction.py",
        "--date", args.main_date, "--period", args.period, "--draw", args.draw,
        "--pred", merged_pred, "--out", merged_verify)
    run("tools/export_blogger_prediction.py",
        "--verify", merged_verify, "--period", args.period, "--draw", args.draw)
    print("\n✅ 期 {} ①–⑤ 完成（默认并入前一天尾池 {} 条）："
          "docs/规律/{}.{{json,md}} 已覆盖".format(args.period, n_tail, args.period))


if __name__ == "__main__":
    main()
