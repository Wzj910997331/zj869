# -*- coding: utf-8 -*-
"""复核后重建命中集：
1. 从 verify_results_final.json 读取逐条判定
2. 输出 data/crawl/20260828/image_patterns_verified.json（含最终verdict）
3. 重建 data/crawl/260828_verified/（按博主分目录，只放 CONFIRM 命中图）+ 命中记录.json
4. 汇总表打印

用法: python tools/rebuild_hits.py --verify data/crawl/20260828/verify_results_final.json \
      --images "C:\\Users\\zhenjie.wu\\.dsh\\work\\gouli_jpg"
"""
import argparse
import json
import os
import shutil
import sys

sys.stdout.reconfigure(errors="replace")

DRAW = "94683"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", required=True, help="verify_results_final.json 路径")
    ap.add_argument("--images", default=r"C:\Users\zhenjie.wu\.dsh\work\gouli_jpg")
    ap.add_argument("--out-verified", default=None,
                    help="输出 verified 记录 json（默认 data/crawl/20260828/image_patterns_verified.json）")
    ap.add_argument("--out-dir", default=None,
                    help="重建图目录（默认 data/crawl/260828_verified）")
    args = ap.parse_args()

    data = json.load(open(args.verify, encoding="utf-8"))
    verdicts = data["verdicts"]
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_verified = args.out_verified or os.path.join(repo, "data", "crawl", "20260828", "image_patterns_verified.json")
    out_dir = args.out_dir or os.path.join(repo, "data", "crawl", "260828_verified")

    confirmed = [v for v in verdicts if v["verdict"] == "CONFIRM"]
    rejected = [v for v in verdicts if v["verdict"] == "REJECT"]
    ambiguous = [v for v in verdicts if v["verdict"] in ("AMBIGUOUS", "ERROR")]
    kills = [v for v in verdicts if v["verdict"] == "KILL"]
    nopos = [v for v in verdicts if v["verdict"] == "NOPOS"]

    print(f"=== 复核汇总 (开奖 {DRAW}) ===")
    print(f"CONFIRM(确认命中) {len(confirmed)} | REJECT(剔除) {len(rejected)} | "
          f"AMBIGUOUS/ERROR {len(ambiguous)} | KILL(杀号事实) {len(kills)} | NOPOS {len(nopos)}")
    print()
    print("--- CONFIRM 明细 ---")
    for v in confirmed:
        print(f"  {v['blogger']} | {v['file'][:24]} | {v['type']} {v['position']} {v['numbers']}")
    print()
    print("--- REJECT 明细 ---")
    for v in rejected:
        print(f"  {v['blogger']} | {v['file'][:24]} | {v['type']} {v['position']} {v['numbers']} | {v['note'][:70]}")
    if ambiguous:
        print()
        print("--- AMBIGUOUS/ERROR 明细 ---")
        for v in ambiguous:
            print(f"  {v['blogger']} | {v['file'][:24]} | {v['type']} {v['position']} {v['numbers']} | {v['note'][:90]}")

    # 输出 verified 记录 json
    os.makedirs(os.path.dirname(out_verified), exist_ok=True)
    with open(out_verified, "w", encoding="utf-8") as f:
        json.dump({"draw": DRAW, "verdicts": verdicts}, f, ensure_ascii=False, indent=1)
    print()
    print("verified 记录:", out_verified)

    # 重建 260828_verified/
    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir)
    hit_record = {"draw": DRAW, "confirm": [], "reject": [], "ambiguous": [], "kill": []}
    for v in confirmed:
        blogger = v["blogger"]
        bdir = os.path.join(out_dir, blogger)
        os.makedirs(bdir, exist_ok=True)
        src = os.path.join(args.images, v["file"])
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(bdir, v["file"]))
        hit_record["confirm"].append({
            "file": v["file"], "type": v["type"], "position": v["position"],
            "numbers": v["numbers"], "note": v["note"],
        })
    for v in rejected:
        hit_record["reject"].append({"file": v["file"], "type": v["type"], "position": v["position"], "numbers": v["numbers"]})
    for v in ambiguous:
        hit_record["ambiguous"].append({"file": v["file"], "type": v["type"], "position": v["position"], "numbers": v["numbers"], "note": v["note"]})
    for v in kills:
        hit_record["kill"].append({"file": v["file"], "type": v["type"], "position": v["position"], "numbers": v["numbers"], "note": v["note"]})
    with open(os.path.join(out_dir, "命中记录.json"), "w", encoding="utf-8") as f:
        json.dump(hit_record, f, ensure_ascii=False, indent=1)

    n_images = sum(len(files) for _, _, files in os.walk(out_dir) if files)
    print(f"重建目录: {out_dir} (含 CONFIRM 博主图 {n_images} 张 + 命中记录.json)")


if __name__ == "__main__":
    main()
