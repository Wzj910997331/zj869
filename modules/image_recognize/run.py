#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run.py — 走势图识别流水线 CLI（stage0 → 1 → 2 → 4）

用法：
  /usr/bin/python3 modules/image_recognize/run.py \
      --blogger 小屁股_483847515 --date 2026-08-28 \
      [--images <png...>]          # 缺省自动发现 data/crawl/<date>/images/*.png
      [--from-stage 0]             # 只跑 N 及以后
      [--model glm-5.3-flash]      # 视觉读数字模型
      [--analysis-model deepseek-v4-flash]  # 叙事总结模型
      [--mode fast|full]           # fast=仅标注行栈；full=全行栈
      [--skip-train]               # CNN 方案已弃用，参数保留占位

阶段产物（每阶段一个 JSON，落 data/recognize/<blogger>/<date>/）：
  manifest.json → grid_geometry.json → crops_manifest.json → patterns.json
+ docs/图片规律识别报告-<blogger>-<date>.md

说明：CNN 数字识别已弃用（合成数据对真实走势图数字不可用），改为
"裁剪规律区域 → glm 视觉读数字 → 规则候选 + deepseek 叙事"（见 README）。
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))  # zj869/
PY = "/usr/bin/python3"


def run_stage(name, cmd, env=None):
    print(f"\n=== {name} ===")
    full = [PY, os.path.join(HERE, cmd[0])] + cmd[1:]
    e = dict(os.environ)
    if env:
        e.update(env)
    r = subprocess.run(full, cwd=HERE, env=e)
    if r.returncode != 0:
        print(f"[run] ❌ {name} 失败 (exit {r.returncode})")
        sys.exit(1)
    print(f"[run] ✅ {name} 完成")


def manifest_path(blogger, date, out):
    if out:
        return out
    return os.path.join(REPO, "data", "recognize", blogger, date, "manifest.json")


def main():
    ap = argparse.ArgumentParser(description="走势图识别流水线")
    ap.add_argument("--blogger", required=True)
    ap.add_argument("--date", default="2026-08-28")
    ap.add_argument("--images", nargs="*", default=None)
    ap.add_argument("--out", default=None, help="manifest.json 路径（覆盖默认）")
    ap.add_argument("--from-stage", type=int, default=0, choices=[0, 1, 2, 4],
                    help="只重跑 N 及以后（3 已并入 stage2 裁剪）")
    ap.add_argument("--model", default="glm-5.3-flash")
    ap.add_argument("--analysis-model", default="deepseek-v4-flash")
    ap.add_argument("--mode", choices=["fast", "full"], default="fast")
    ap.add_argument("--skip-train", action="store_true", help="CNN 已弃用，占位")
    args = ap.parse_args()

    mpath = manifest_path(args.blogger, args.date, args.out)
    if args.from_stage <= 0:
        cmd = ["stage0_input.py", "--blogger", args.blogger, "--date", args.date]
        if args.images:
            cmd += ["--images"] + args.images
        if args.out:
            cmd += ["--out", args.out]
        run_stage("stage0 输入清单", cmd)
    if not os.path.exists(mpath):
        print(f"[run] 读不到 {mpath}（先跑 stage0 或 --from-stage 0）")
        sys.exit(2)

    if args.from_stage <= 1:
        run_stage("stage1 网格几何", ["stage1_preprocess.py", "--manifest", mpath])
    if args.from_stage <= 2:
        run_stage("stage2 规律区域裁剪", ["stage2_crop.py", "--manifest", mpath])
    if args.from_stage <= 4:
        run_stage("stage4 读数字+规律+叙事",
                  ["stage4_llm.py", "--manifest", mpath,
                   "--model", args.model, "--analysis-model", args.analysis_model,
                   "--mode", args.mode])
    print(f"\n[run] 全部完成 -> {os.path.dirname(mpath)} + docs 报告")


if __name__ == "__main__":
    main()
