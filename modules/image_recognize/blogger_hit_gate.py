#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""blogger_hit_gate.py — 第1道门：确定性 CV 判「博主是否在目标期行手写预测」（有无）。

只做 OpenCV（读图 + 色彩标注 blob 检测），不调视觉模型、不调 LLM。

命中基准口径（用户 2026-09-02 拍板）：命中只认博主**目标期行手写**的数字，不是
「看博主画的历史圈 + 程序自摸规律」。本门只判**有无**预测（目标期行有无彩标/笔迹），
数字值交给下一步 `read_blogger_prediction.py` 用视觉读（手写数字非印刷体，CNN 读不出）。

目标期行定位（关键，v2 修正「目标期≠最下行」）：
  - 排列5走势图纵排，**期号越新 y 越大**（2633 底 / 2619 顶）。目标期 26231 行**不一定是
    最下行**——博主图常含 26231 之后的空行（26232/26233）。用户实测：一张图里 26230(开奖94683)
    下是 26231/26232/26233，预测"7"粉圈写在 26231 行个位；底行 26233 是空的。
  - 故用 filter_report.period_pairs（期号→像素 y，filter_trend 自身期号匹配，含空行期号标签）
    定位目标期像素 y；缺匹配则用校准期(26230)+row_pitch 推算下一行；还定位不到 → **保守 pass**
    （宁多送视觉不错过真预测）。
  - 目标期行带 = [y - row_half, y + row_half]；该带内有任一彩色标注 blob（detect_annotations）
    即 pass（博主在目标期行写了彩标/预测数字），否则 skip（仅回看历史）。

用法:
  python3 modules/image_recognize/blogger_hit_gate.py --date 20260829 \
      --filter data/crawl/20260829/filter_report.json \
      --images data/crawl/20260829/images \
      --target-period 26231 --calib-period 26230 --calib-draw "9 4 6 8 3" \
      --out data/crawl/20260829/blogger_hit_gate.json
"""
import argparse
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _REPO)

from modules.image_recognize.cv_trend_reader.reader import (  # noqa: E402
    detect_annotations, load,
)

KEEP_DECISIONS = {"keep-high", "keep-med", "uncertain"}


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def target_y(ent, target_period, calib_period, row_pitch):
    """定位目标期像素 y。返回 (y, source)。source ∈ {period, calib, None}。

    - period: period_pairs 里有期号==目标期 → 其 pixel y。
    - calib:  period_pairs 有期号==校准期 → 该校准行 + row_pitch（下一行=目标期）。
    - None:   定位不到（调用方应保守 pass）。
    """
    pp = ent.get("period_pairs") or []
    for p in pp:
        if p.get("period") == target_period and p.get("row") is not None:
            return p["row"], "period"
    for p in pp:
        if p.get("period") == calib_period and p.get("row") is not None:
            return p["row"] + (row_pitch or 0), "calib"
    return None, None


def gate_image(name, ent, images_dir, target_period, calib_period):
    """判单图：目标期行有无彩标。返回 (file, gate, has, target_y, evidence, reason)。"""
    img_path = os.path.join(images_dir, name)
    if not os.path.exists(img_path):
        return {"file": name, "gate": "pass", "has_target_prediction": None,
                "reason": "缺图不可核验(保守送视觉)", "target_y": None}

    row_pitch = ent.get("row_pitch") or 0
    row_half = ent.get("row_half") or 0
    ty, src = target_y(ent, target_period, calib_period, row_pitch)

    # 定位不到 → 保守 pass（宁多送视觉不错过真预测）
    if ty is None:
        return {"file": name, "gate": "pass", "has_target_prediction": None,
                "target_y": None, "reason": "目标期未定位(期号/校准均无匹配)，保守送视觉",
                "evidence": f"period_pairs={ent.get('period_pairs') or '[]'}"}

    try:
        img = load(img_path)
    except Exception as e:
        return {"file": name, "gate": "pass", "has_target_prediction": None,
                "target_y": ty, "reason": f"读图失败({e})，保守送视觉"}

    # 目标期行带：用整行高(至少 row_half、row_pitch//2、下限40)作半宽，
    # 把博主在目标期行的斜连/连线/圈选(可能稍偏上/下)都算进去；又不至于混入校准行(上相邻行)标注。
    half = max(row_half or 0, row_pitch // 2 if row_pitch else 0, 40)
    y0, y1 = ty - half, ty + half
    blobs = detect_annotations(img)  # {color: [(x,y,w,h,area,kind)]}
    hit_blobs = []
    for color, items in blobs.items():
        for (x, y, w, h, area, kind) in items:
            cy = y + h // 2
            if y0 <= cy <= y1:
                hit_blobs.append((color, x, y, w, h, kind))
    has = bool(hit_blobs)
    desc = "; ".join(f"{c}@{x},{y}-{w}x{h}({kind})" for c, x, y, w, h, kind in sorted(
        hit_blobs, key=lambda b: -b[4]))
    return {"file": name, "gate": "pass" if has else "skip",
            "has_target_prediction": has,
            "target_y": ty, "target_source": src,
            "half": int(half),
            "n_target_blobs": len(hit_blobs),
            "evidence": f"目标期行[ty={ty}±{int(half)}]带内彩标[{desc}]" if has
                        else f"目标期行[ty={ty}±{int(half)}]带内无彩标（{len(blobs)} 个标注均不在此行）",
            "reason": None if has else "未在目标期行写预测（无彩标在该行，博主仅回看历史）"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="爬取日期，如 20260829")
    ap.add_argument("--filter", required=True, help="filter_report.json 路径")
    ap.add_argument("--images", required=True, help="原图目录 data/crawl/<date>/images")
    ap.add_argument("--target-period", required=True, help="目标期号，如 26231")
    ap.add_argument("--calib-period", default="", help="校准期号，如 26230")
    ap.add_argument("--calib-draw", default="", help="校准期开奖，如 '9 4 6 8 3'")
    ap.add_argument("--out", default=None, help="输出 blogger_hit_gate.json 路径")
    args = ap.parse_args()

    report = read_json(args.filter)
    imgs = report["images"]
    keep = {k: v for k, v in imgs.items() if v.get("decision") in KEEP_DECISIONS}

    results = [gate_image(k, v, args.images, args.target_period, args.calib_period)
               for k, v in keep.items()]

    n_pass = sum(1 for r in results if r["gate"] == "pass")
    n_skip = len(results) - n_pass
    n_unlocated = sum(1 for r in results if r.get("target_y") is None)

    print(f"keep/uncertain 图总数: {len(results)}")
    print(f"  门 pass（目标期行有彩标→接视觉读手写）: {n_pass}  （其中目标期未定位保守放行: {n_unlocated}）")
    print(f"  门 skip （目标期行无彩标→仅回看历史，不送视觉）: {n_skip}")

    if args.out:
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({
                "date": args.date,
                "target_period": args.target_period,
                "calib_period": args.calib_period,
                "generated_by": "blogger_hit_gate.py",
                "n_keep": len(results),
                "n_pass": n_pass,
                "n_skip": n_skip,
                "n_target_unlocated": n_unlocated,
                "gate": "CV 判目标期行(=period_pairs 定位)有无彩标；pass→接视觉读手写，skip→博主仅回看历史；目标期未定位→保守 pass",
                "images": {r["file"]: r for r in results},
            }, f, ensure_ascii=False, indent=1)
        print(f"  → 写入 {args.out}")


if __name__ == "__main__":
    main()
