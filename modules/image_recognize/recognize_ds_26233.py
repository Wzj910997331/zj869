#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""recognize_ds_26233.py — 26233 期博主画法视觉识别(ds 全用)。

复刻 20260828/vision_patterns_full.json 结构，但用 deepseek-v4-flash-vision-exp：
对每张 ds-ok 博主的**原图**，识别博主画的所有预测标注(圈选/斜连/定位/胆码/杀号/和值等)。
输出 {file, type, period, patterns[{type,position,numbers,desc}]}，供方法总结层使用。

用法:
  /usr/bin/python3 modules/image_recognize/recognize_ds_26233.py \
      --date 20260831 --target-period 26233 --target-draw "1 6 3 4 0" \
      [--limit N --resume --workers 4]
"""
import argparse
import base64
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "modules", "image_recognize"))
import analyze_crops_ds as ac  # noqa: E402  (复用 DS_MODEL/BASE_URL/AUTH_TOKEN/call_ds_vision/DsConnError)


def read_b64(path):
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    mime = "image/png" if path.lower().endswith(".png") else "image/jpeg"
    return f"data:{mime};base64,{b64}"


def build_prompt(filename, period):
    return f"""这张图是博主在某期排列五走势图上手画的预测标注(已裁剪为标注行栈，每行左侧红色 row 标签为行号；数字区从左到右为 万/千/百/十/个 位)。
博主预测的目标期是 {period} 期。
请判断图片类型(走势图圈选/文字预测截图/杀号表/其他)。若是走势图圈选，列出博主画的所有预测标注：
每条含 type(定位/斜连/胆码/头/尾/和值/杀号)、position(万/千/百/十/个位，无位置则 null)、
numbers(数字列表)、desc(一句话，含标注方式如"圈选/斜线连接/手写"与预测数字)。
博主常画 2-4 个位置，务必全列。只回 JSON，不要额外文字：
{{"type":"","period":"{period}","patterns":[{{"type":"","position":null,"numbers":[],"desc":""}}]}}"""


def extract_json(text):
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    i, j = text.find("{"), text.rfind("}")
    if i >= 0 and j > i:
        try:
            return json.loads(text[i:j + 1])
        except Exception:
            pass
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="20260831")
    ap.add_argument("--target-period", default="26233")
    ap.add_argument("--target-draw", default="1 6 3 4 0")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--max-tokens", type=int, default=16000)
    args = ap.parse_args()

    analyze_path = os.path.join(REPO, "data", "recognize", f"{args.date}_all",
                                "analysis", f"analyze_{args.date}.json")
    an = json.load(open(analyze_path, encoding="utf-8"))["images"]
    manifest_path = os.path.join(REPO, "data", "recognize", f"{args.date}_all",
                                 "crops_all_manifest.json")
    crops = json.load(open(manifest_path, encoding="utf-8"))["images"]
    files = sorted(f for f, r in an.items() if r.get("decision") == "ds-ok")
    if args.offset:
        files = files[args.offset:]
    if args.limit:
        files = files[:args.limit]
    crops_root = os.path.join(REPO, "data", "recognize", f"{args.date}_all")
    out = os.path.join(REPO, "data", "crawl", args.date, "vision_patterns_full.json")

    existing = {}
    if args.resume and os.path.exists(out):
        try:
            for v in json.load(open(out, encoding="utf-8")):
                existing[v["file"]] = v
            print(f"resume: 已有 {len(existing)} 条")
        except Exception as e:
            print(f"警告: 读取 {out} 失败（{e}），从头识别")
            existing = {}
    todo = [f for f in files if f not in existing or existing[f].get("error")]
    print(f"识别 {len(todo)} 张 ds-ok 裁剪图(02_annotated.png) @ {ac.DS_MODEL} workers={args.workers}")

    def one(f):
        rec = crops.get(f)
        if not rec or not rec.get("crop_dir"):
            return {"file": f, "type": "其他", "period": args.target_period,
                    "patterns": [], "error": "无裁剪产物"}
        img = os.path.join(crops_root, rec["crop_dir"], "02_annotated.png")
        if not os.path.exists(img):
            return {"file": f, "type": "其他", "period": args.target_period,
                    "patterns": [], "error": "裁剪图缺失"}
        uri = read_b64(img)
        prompt = build_prompt(f, args.target_period)
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": uri}}]}]
        to = args.timeout
        for attempt in range(1, 4):
            try:
                raw = ac.call_ds_vision(msgs, timeout=to, max_tokens=args.max_tokens)
                if not raw:
                    # 超时/空：网关慢，逐次加大 timeout 重试（60→90→120）
                    to = min(150, to + 30)
                    time.sleep(2 * attempt)
                    continue
                v = extract_json(raw)
                if v is None:
                    if attempt >= 2:
                        return {"file": f, "type": "其他", "period": args.target_period,
                                "patterns": [], "error": "JSON解析失败"}
                    time.sleep(2 * attempt)
                    continue
                v.setdefault("file", f)
                v.setdefault("type", "其他")
                v.setdefault("patterns", [])
                return v
            except ac.DsConnError:
                return {"file": f, "type": "其他", "period": args.target_period,
                        "patterns": [], "error": "conn"}
            except Exception as e:
                time.sleep(2 * attempt)
                if attempt >= 2:
                    return {"file": f, "type": "其他", "period": args.target_period,
                            "patterns": [], "error": str(e)[:80]}
        return {"file": f, "type": "其他", "period": args.target_period,
                "patterns": [], "error": "failed"}

    results, n_ok, n_err = [], 0, 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(one, f): f for f in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            r = fut.result()
            results.append(r)
            if r.get("error"):
                n_err += 1
                print(f"  [{i}/{len(todo)}] ✗ {r['file']}: {r['error']}")
            else:
                n_ok += 1
                print(f"  [{i}/{len(todo)}] ✓ {r['file']} type={r.get('type')} "
                      f"patterns={len(r.get('patterns') or [])}")

    merged = {}
    for v in existing.values():
        merged[v["file"]] = v
    for v in results:
        merged[v["file"]] = v
    merged_list = [merged[f] for f in sorted(merged)]
    with open(out, "w", encoding="utf-8") as f:
        json.dump(merged_list, f, ensure_ascii=False, indent=1)
    n_has = sum(1 for v in merged_list if (v.get("patterns") or []))
    print(f"\n完成 {n_ok} 成功 / {n_err} 失败，共 {len(merged_list)} 条，"
          f"含标注 {n_has} 张 -> {out}")


if __name__ == "__main__":
    main()
