#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ab_shrink_test.py — 缩小提速 A/B 验证。

对抽样的几张图，同一 prompt 分别用【原图】与【缩小图(长边512+JPEG q80)】识别一次，
对比 GLM 的耗时与识别内容一致性（patterns 的 type/position/numbers 是否吻合）。

用法:
  python tools/ab_shrink_test.py --base 20260830 --period 26232 --calib 26231 \
      --calib-draw "1 8 7 9 9" --crops-dir data/recognize/20260830_all --n 6
"""
import argparse
import importlib.util
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 复用 recognize_patterns.py 的 shrink_image / call_vision / build_crops_prompt / extract_json
spec = importlib.util.spec_from_file_location(
    "rp", os.path.join(REPO, "tools", "recognize_patterns.py"))
rp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rp)


def pattern_key(p):
    """识别内容的可比对指纹：type+position+排序后numbers。忽略 desc。"""
    return (p.get("type"), p.get("position"), tuple(sorted(p.get("numbers") or [])))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="data/crawl/YYYYMMDD 数字段")
    ap.add_argument("--period", required=True)
    ap.add_argument("--calib", required=True)
    ap.add_argument("--calib-draw", required=True)
    ap.add_argument("--crops-dir", required=True)
    ap.add_argument("--n", type=int, default=6, help="抽样张数")
    args = ap.parse_args()

    api_key = rp.load_api_key()
    if not api_key:
        print("找不到 DEEPSEEK1_API_KEY")
        sys.exit(1)

    BASE = os.path.join(REPO, "data", "crawl", args.base)
    d = json.load(open(os.path.join(BASE, "vision_patterns_full.json"), encoding="utf-8"))
    man = json.load(open(os.path.join(args.crops_dir, "crops_all_manifest.json"), encoding="utf-8"))["images"]

    # 抽样：先 error（empty/超时大图），再补有效图
    errs = [v for v in d if v.get("error")]
    oks = [v for v in d if not v.get("error")]
    sample = errs[:args.n // 2] + oks[:args.n - len(errs[:args.n // 2])]

    def img_path(f):
        rec = man.get(f)
        if rec and rec.get("crop_dir"):
            p = os.path.join(args.crops_dir, rec["crop_dir"], "02_annotated.png")
            if os.path.exists(p):
                return p
        return os.path.join(BASE, "images", f)

    print(f"抽样 {len(sample)} 张（{len([v for v in sample if v.get('error')])} error + {len([v for v in sample if not v.get('error')])} 有效）")
    rows = []
    for v in sample:
        f = v["file"]
        img = img_path(f)
        if not os.path.exists(img):
            print(f"  [skip] {f} 图缺失")
            continue
        prompt = rp.build_crops_prompt(f, args.period)

        def try_recognize(shrink):
            """单路识别：失败返回异常字符串，不崩。原图给 180s（大图），缩小图 90s。"""
            t0 = time.time()
            try:
                raw = rp.call_vision(api_key, img, prompt,
                                     timeout=(180 if not shrink else 90), shrink=shrink)
                return raw, rp.extract_json(raw), time.time() - t0
            except Exception as e:
                return f"[异常] {str(e)[:60]}", None, time.time() - t0

        raw0, r0, dt0 = try_recognize(False)
        raw1, r1, dt1 = try_recognize(True)

        p0 = sorted(pattern_key(p) for p in (r0 or {}).get("patterns", []))
        p1 = sorted(pattern_key(p) for p in (r1 or {}).get("patterns", []))
        # 任一识别失败（r0/r1 为 None）→ 一致性按 False 记，避免"双空==双空"误判
        same = (r0 is not None and r1 is not None) and set(p0) == set(p1)
        rows.append({
            "file": f, "error_prev": v.get("error", ""),
            "原图": {"type": (r0 or {}).get("type"), "n_patterns": len(p0), "耗时s": round(dt0, 1),
                     "patterns": p0, "raw_error": "" if r0 else (raw0 or "")[:40]},
            "缩小图": {"type": (r1 or {}).get("type"), "n_patterns": len(p1), "耗时s": round(dt1, 1),
                      "patterns": p1, "raw_error": "" if r1 else (raw1 or "")[:40]},
            "内容一致": same,
        })
        print(f"  {f}")
        print(f"    原图:   {round(dt0,1):>5}s type={(r0 or {}).get('type')} patterns={len(p0)}" +
              (f"  [失败 {str(raw0)[:30]}]" if not r0 else ""))
        print(f"    缩小图: {round(dt1,1):>5}s type={(r1 or {}).get('type')} patterns={len(p1)}" +
              (f"  [失败 {str(raw1)[:30]}]" if not r1 else ""))
        print(f"    内容一致: {'✓' if same else '✗ 差异=' + str(set(p0) ^ set(p1))}")

    out = os.path.join(BASE, "ab_shrink_test.json")
    json.dump({"说明": f"缩小提速 A/B（{args.base} 期 {args.period}，原图 vs 长边512+JPEGq80）",
               "rows": rows}, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    ok = [r for r in rows if r["内容一致"] and r["原图"]["raw_error"] == "" and r["缩小图"]["raw_error"] == ""]
    times0 = [r["原图"]["耗时s"] for r in rows if r["原图"]["raw_error"] == ""]
    times1 = [r["缩小图"]["耗时s"] for r in rows if r["缩小图"]["raw_error"] == ""]
    print()
    print(f"内容一致率: {len(ok)}/{len(rows)}")
    if times0 and times1:
        print(f"耗时中位: 原图 {sorted(times0)[len(times0)//2]:.1f}s → 缩小图 {sorted(times1)[len(times1)//2]:.1f}s"
              f"（{'%.1fx 提速' % (sorted(times0)[len(times0)//2] / max(sorted(times1)[len(times1)//2], 0.1)) if times1 else ''}）")
    print(f"结果 → {out}")


if __name__ == "__main__":
    main()
