#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_period.py — 全期**受监控**总驱动（watchdog 串起 爬取→开奖→①–⑤→⑥⑦→定稿）。

背景：一个期的博主单押全流程本要人肉按序敲 5+ 条命令，任一阶段卡死（GLM/DS 网关超时、
filter tesseract 自打负载、crawl 网络悬挂）只能肉眼盯终端，分不清卡哪步。本工具把每个阶段
交给 tools/stage_runner.py 的 run_stage —— 无输出心跳 + 每阶段 wall 硬顶，卡死即 kill 进程组
(含孙进程) + 写诊断 JSON 到 logs/watchdog/，靠各步产物幂等重跑同一条命令即从断点续。

阶段（每步产物已存在则跳过/复用，kill 后重跑同一条 run_period 即续跑）：
  1  crawl     （可选）crawl_gouli.py 补缺失自然日（日目录已有 posts.json + 非空 images/ 则跳过）
  2  fetch     开奖表 fetch_lottery.py → data/crawl/<main-date>/lottery_recent.json
                 （该文件已含 target period 的开奖记录则跳过）
  3  ①–⑤       博主单押 include_prevday_tail.py（filter→gate→extract→read→verify→export docs）
                 ——内部各步已各自走 watchdog，本层把整条链再兜一层大预算
  4  ⑥⑦       画规自证复现 run_guihua_verify.py（仅当 ③ 导出的 docs hit_records>0）
  5  定稿       finalize_period_docs.py → 权威 docs/规律/<period>.{json,md}
                 （仅当 ④ 的 verdict 文件存在）

用法（一键跑一期，从第一步到文档收尾）:
  python3 tools/run_period.py \
      --period 26230 --draw "9 4 6 8 3" \
      --calib-period 26229 --calib-draw "2 8 0 5 4" --main-date 20260828

可选（透传/控制阶段）:
  --prev-date 20260827      # 前一天 crawl 目录（默认 main-date 前一天）
  --no-tail                 # 只跑主池不并入前一天尾池（透传 include_prevday_tail）
  --prev-cutoff "21:30"     # 尾池发帖起点，默认前一日 21:30（透传）
  --lottery <path>          # 开奖表（默认 data/crawl/<main-date>/lottery_recent.json）
  --skip-crawl / --skip-guihua / --skip-finalize   # 跳过对应阶段（幂等可重进看采集）
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # 使 stage_runner 可 import
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(REPO, "tools")
DOCS_DIR = os.path.join(REPO, "docs", "规律")
DATA_DIR = os.path.join(REPO, "data", "crawl")

from stage_runner import run_stage  # noqa: E402


def read_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


_DRY = False  # --dry: 只打印每个阶段会跑什么，不执行（本机产物现状定 gate）


def stage(label, cmd, logdir):
    """包一层 watchdog：非 0（含 124 wall / 125 idle）→ 打续跑提示后退出。"""
    if _DRY:
        print(f"[dry] 阶段 {label}（不执行）: {' '.join(cmd)}")
        return 0
    rc = run_stage(cmd, label=label, logdir=logdir)
    if rc != 0:
        why = {124: "wall 超预算", 125: "idle 卡死(无输出)"}.get(rc, f"退出码 {rc}")
        sys.exit(f"\n✗ 阶段 {label} {why} → 诊断 logs/watchdog/；"
                 f"重跑同一条 run_period 命令即幂等续跑")
    return rc


def day_iso(ymd):
    return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"


def day_dir_present(ymd):
    d = os.path.join(DATA_DIR, ymd)
    posts, imgs = os.path.join(d, "posts.json"), os.path.join(d, "images")
    try:
        return os.path.isfile(posts) and os.path.isdir(imgs) and bool(os.listdir(imgs))
    except OSError:
        return False


def lottery_has_period(lottery, period):
    if not os.path.exists(lottery):
        return False
    try:
        data = read_json(lottery)
    except Exception:
        return False
    recs = data if isinstance(data, list) else (data.get("records") or data.get("data") or [])
    return any(str(r.get("period")) == str(period) for r in recs)


def ensure_gverify_images(main_date, prev_date):
    """命中原图合并视图：main-date images + prev-date images 按文件名 symlink 并集。

    run_guihua_verify 只收一个 --images 目录，而命中图可能来自主池(开奖日)或前一天尾池
    (前一自然日晚发帖) → 用此视图让每个命中 file 都能按名打开。幂等：已存在的 symlink 跳过。
    """
    view = os.path.join(DATA_DIR, f"{main_date}_gverify_images")
    os.makedirs(view, exist_ok=True)
    srcs = [os.path.join(DATA_DIR, main_date, "images")]
    if prev_date:
        p = os.path.join(DATA_DIR, prev_date, "images")
        if os.path.isdir(p):
            srcs.append(p)
    n_new = 0
    for s in srcs:
        try:
            files = os.listdir(s)
        except OSError:
            continue
        for f in sorted(files):
            dst = os.path.join(view, f)
            if not os.path.lexists(dst):
                try:
                    os.symlink(os.path.join(s, f), dst)
                    n_new += 1
                except OSError:
                    pass
    print(f"[view] 命中原图合并视图: {view}（共 {len(os.listdir(view))} 张，新 symlink {n_new}）")
    return view


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", required=True)
    ap.add_argument("--draw", required=True)
    ap.add_argument("--calib-period", default="")
    ap.add_argument("--calib-draw", default="")
    ap.add_argument("--main-date", required=True, help="开奖日 crawl 目录，如 20260828")
    ap.add_argument("--prev-date", default=None, help="前一天 crawl 目录(默认 main-date 前一天)")
    ap.add_argument("--no-tail", action="store_true", help="只跑主池，不并入前一天尾池")
    ap.add_argument("--prev-cutoff", default=None,
                    help="尾池发帖起点(默认前一日 21:30)，透传 include_prevday_tail")
    ap.add_argument("--lottery", default=None, help="开奖表(默认 main-date/lottery_recent.json)")
    ap.add_argument("--skip-crawl", action="store_true", help="跳过 crawl 阶段")
    ap.add_argument("--skip-guihua", action="store_true", help="跳过 ⑥⑦ 画规自证阶段")
    ap.add_argument("--skip-finalize", action="store_true", help="跳过定稿阶段")
    ap.add_argument("--dry", action="store_true",
                    help="排练：按本机产物现状打印每阶段将执行什么，不真跑")
    args = ap.parse_args()

    global _DRY
    _DRY = args.dry

    logdir = os.path.join(REPO, "logs", "watchdog", args.main_date)
    main_dir = os.path.join(DATA_DIR, args.main_date)
    prev_date = args.prev_date or (datetime.strptime(args.main_date, "%Y%m%d")
                                   - timedelta(days=1)).strftime("%Y%m%d")
    lottery = args.lottery or os.path.join(main_dir, "lottery_recent.json")
    docs_p = os.path.join(DOCS_DIR, f"{args.period}.json")
    verdict_p = os.path.join(main_dir, f"guihua_{args.period}_reproducible.verdict.json")

    n_phase = 0
    print(f"\n===== 期 {args.period} 全流程（{args.main_date}，watchdog 逐阶段监控）=====")

    # ---------- 1. crawl（缺的日目录才爬；--no-tail 时前一天非必需）----------
    n_phase += 1
    need = [prev_date] if not args.no_tail else []
    need.append(args.main_date)
    print(f"\n── [阶段 {n_phase}/5] crawl 补爬 ──")
    missing = [d for d in need if not day_dir_present(d)]
    if missing:
        if args.skip_crawl:
            print(f"[crawl] --skip-crawl，跳过（仍缺: {missing}）")
        else:
            missing.sort()
            sub = [day_iso(missing[0])] if len(missing) == 1 else \
                  [day_iso(missing[0]), day_iso(missing[-1])]
            print(f"[crawl] 缺 {missing} → crawl_gouli {' '.join(sub)}")
            stage("crawl_gouli.py",
                  [sys.executable, os.path.join(TOOLS, "crawl_gouli.py"), *sub], logdir)
    else:
        print(f"[crawl] 日目录已齐: {need} → 跳过爬取")

    # ---------- 2. fetch 开奖表 ----------
    n_phase += 1
    print(f"\n── [阶段 {n_phase}/5] fetch 开奖表 ──")
    if not os.path.isdir(main_dir):
        sys.exit(f"✗ 主池目录不存在: {main_dir}（先去掉 --skip-crawl 或先爬 20260828）")
    if not lottery_has_period(lottery, args.period):
        print(f"[fetch] {lottery} 缺 期{args.period} 开奖 → fetch_lottery")
        stage("fetch_lottery.py",
              [sys.executable, os.path.join(TOOLS, "fetch_lottery.py"), "--out", lottery],
              logdir)
    else:
        print(f"[fetch] {lottery} 已含 期{args.period} → 跳过")

    # ---------- 3. ①–⑤ 博主单押（含尾池并入；内部各步已各自 watch，本层再兜大预算）----------
    n_phase += 1
    print(f"\n── [阶段 {n_phase}/5] ①–⑤ 博主单押（include_prevday_tail）──")
    inc = [sys.executable, os.path.join(TOOLS, "include_prevday_tail.py"),
           "--period", args.period, "--draw", args.draw,
           "--calib-period", args.calib_period, "--calib-draw", args.calib_draw,
           "--main-date", args.main_date, "--lottery", lottery]
    if prev_date:
        inc += ["--prev-date", prev_date]
    if args.no_tail:
        inc += ["--no-tail"]
    if args.prev_cutoff:
        inc += ["--prev-cutoff", args.prev_cutoff]
    stage("include_prevday_tail.py", inc, logdir)

    if not os.path.exists(docs_p):
        sys.exit(f"✗ ①–⑤ 未产出 {docs_p}")
    hits = int(read_json(docs_p).get("hit_records", 0))
    print(f"[①–⑤] docs/规律/{args.period}.json hit_records = {hits}")

    # ---------- 4. ⑥⑦ 画规自证复现（仅当有命中图）----------
    n_phase += 1
    print(f"\n── [阶段 {n_phase}/5] ⑥⑦ 画规自证复现（run_guihua_verify）──")
    if hits <= 0:
        print("[⑥⑦] hit_records=0 → 无命中图可送画规，跳过（docs 以 ⑤ 导出版收尾）")
    elif args.skip_guihua:
        print("[⑥⑦] --skip-guihua → 跳过（v7 verdict 若已存在，定稿阶段仍会复用）")
    else:
        if _DRY:
            view = os.path.join(DATA_DIR, f"{args.main_date}_gverify_images")
        else:
            view = ensure_gverify_images(args.main_date, prev_date)
        stage("run_guihua_verify.py",
              [sys.executable, os.path.join(TOOLS, "run_guihua_verify.py"),
               "--period", args.period, "--draw", args.draw,
               "--calib", args.calib_period, "--calib-draw", args.calib_draw,
               "--hits", docs_p, "--images", view, "--lottery", lottery,
               "--outdir", main_dir], logdir)

    # ---------- 5. 定稿（合成权威版 docs；仅当 ⑦ verdict 在）----------
    n_phase += 1
    print(f"\n── [阶段 {n_phase}/5] 定稿（finalize_period_docs）──")
    if args.skip_finalize:
        print("[定稿] --skip-finalize → 跳过（docs 保持当前版本）")
    elif not os.path.exists(verdict_p):
        print(f"[定稿] {verdict_p} 不存在 → 跳过（无 ⑦ 判决；docs 保持 ⑤ 导出版）")
    else:
        repro_p = os.path.join(main_dir, f"guihua_{args.period}_reproducible.json")
        if not os.path.exists(repro_p):
            print(f"[定稿] ⚠ {repro_p} 不存在，跳过定稿")
        else:
            stage("finalize_period_docs.py",
                  [sys.executable, os.path.join(TOOLS, "finalize_period_docs.py"),
                   "--docs", docs_p, "--verdict", verdict_p, "--repro", repro_p], logdir)

    print(f"\n✅ 期 {args.period} 全流程完成（产物: {docs_p} + docs/规律/{args.period}.md）")


if __name__ == "__main__":
    main()
