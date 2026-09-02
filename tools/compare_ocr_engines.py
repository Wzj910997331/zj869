#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OCR 引擎 A/B 对比 — tesseract 基线 vs digit_cnn 本地识别

对比两个 filter_report.json 的决策分布/期号置信/逐图差异。
tesseract 基线 = 历史跑的 data/crawl/<date>/filter_report.json;
CNN 版 = `OCR_ENGINE=cnn ... filter_trend.py --out <out>` 产出。

用法:
  OCR_ENGINE=cnn /usr/bin/python3 modules/image_recognize/filter_trend.py \\
      --date 20260831 --target-period 26233 \\
      --lottery data/crawl/20260831/lottery_recent.json \\
      --out data/crawl/20260831/filter_report_cnn.json
  /usr/bin/python3 tools/compare_ocr_engines.py \\
      data/crawl/20260831/filter_report.json data/crawl/20260831/filter_report_cnn.json
"""
import argparse
import collections
import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def dist(imgs, key):
    return dict(collections.Counter(v.get(key) for v in imgs.values()))


def period_dist(imgs):
    d = collections.defaultdict(int)
    for v in imgs.values():
        d[len(v.get("period_matched") or [])] += 1
    return dict(sorted(d.items()))


def main():
    ap = argparse.ArgumentParser(description="filter_report A/B 对比 (tesseract vs CNN)")
    ap.add_argument("base", help="tesseract 基线 filter_report.json")
    ap.add_argument("cnn", help="CNN 版 filter_report.json")
    args = ap.parse_args()

    base = json.load(open(args.base, encoding="utf-8"))["images"]
    cnn = json.load(open(args.cnn, encoding="utf-8"))["images"]
    print("=== 决策分布 ===")
    print(f"  tesseract: {json.dumps(dist(base, 'decision'), ensure_ascii=False)}")
    print(f"  CNN      : {json.dumps(dist(cnn, 'decision'), ensure_ascii=False)}")
    print("=== period_conf ===")
    print(f"  tesseract: {json.dumps(dist(base, 'period_conf'))}")
    print(f"  CNN      : {json.dumps(dist(cnn, 'period_conf'))}")
    print("=== 每图 matched 期号数量分布 ===")
    print(f"  tesseract: {json.dumps(period_dist(base))}")
    print(f"  CNN      : {json.dumps(period_dist(cnn))}")

    diff = {f: (base[f]["decision"], cnn[f]["decision"]) for f in base
            if base[f]["decision"] != cnn[f]["decision"]}
    print(f"\n=== 决策差异 {len(diff)} 张 ===")
    print("  迁移: ", dict(collections.Counter(f"{a}→{b}" for a, b in diff.values())))
    pcd = {f: (base[f].get("period_conf"), cnn[f].get("period_conf")) for f in base
           if base[f].get("period_conf") != cnn[f].get("period_conf")}
    print(f"  period_conf 差异 {len(pcd)} 张: ",
          dict(collections.Counter(f"{a}→{b}" for a, b in pcd.values())))
    for f in list(diff)[:10]:
        print(f"    {f[:48]}: {diff[f][0]} → {diff[f][1]}")


if __name__ == "__main__":
    main()
