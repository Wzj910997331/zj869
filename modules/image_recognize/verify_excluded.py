#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_excluded.py — no-anno/no-grid 图二次校验（视觉模型确认后再剔除）。

背景：crop_all.py 纯 OpenCV 快筛把"无博主标注"图判为 no-anno/no-grid 写入 exclude_list。
这些是疑似图（12 张级），不能直接剔除——万一 OpenCV 漏检（标注太淡/版式太特殊）会误杀真图。
本脚本对每张疑似图 resize 后让视觉模型（deepseek-v4-flash-vision-exp）二次确认：

  - 确认无规律（无标注 / 非走势图）→ decision=excluded  剔除，不进后续分析
  - 视觉发现确有标注 / 确是走势图   → decision=keep      OpenCV 漏检，捞回（报告列出）
  - 视觉读不了                       → decision=unknown   保持排除，报告单独列出待人工

仅处理可疑图，不重蹈"全量视觉 90min"覆辙（12 张 × ~10s ≈ 2min，仍在用户 5min 目标内）。

用法：
  /usr/bin/python3 modules/image_recognize/verify_excluded.py --date 20260830 [--limit N]

输出：data/recognize/<date>_all/verify_excluded.json + 回写 exclude_list.json 每项加 decision。
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.request

import cv2

from common import REPO, load_json, write_json, fix_print

VISION_MODEL = "deepseek-v4-flash-vision-exp"
BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "http://llm.riverbegin.cn")
AUTH_TOKEN = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
MAX_W, MAX_H = 1024, 1800     # resize 上限（超长图缩小防死循环；博主标注是粗色带，缩小仍可辨）
VISION_TIMEOUT = 45           # 单次视觉调用限时：>45s 即推理死循环，放弃（与 stage4_direct 同）
TMP_DIR = "/tmp/verify_anno"  # 临时图统一放 /tmp，绝不写进 images/（防 s_2_ glob 污染）

PROMPT = """你是彩票走势图审查助手。判断这张图（1）是否是走势图（有逐期开奖号码表格，每行5个数字）；
（2）博主是否画了规律标注（彩色色带/线段/圆圈/方框，覆盖在数字区）。
只输出一个合法JSON，不要多余文字：
{"is_chart": true/false, "has_annotation": true/false, "note": "一句话说明依据"}"""


def call_vision(messages, timeout=VISION_TIMEOUT):
    """有界视觉调用：与 stage4_direct 同，该模型'始终思考'型，max_tokens 必须≥16000。"""
    body = json.dumps({"model": VISION_MODEL, "messages": messages,
                       "max_tokens": 16000}).encode()
    req = urllib.request.Request(f"{BASE_URL}/v1/chat/completions", data=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {AUTH_TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            j = json.loads(r.read())
        return j["choices"][0]["message"].get("content", "")
    except Exception as e:
        print(f"    [verify] 视觉调用失败: {str(e)[:100]}")
        return None


def parse_verify(text):
    """模型回答 → {"is_chart", "has_annotation", "note"}，容错抽 JSON。"""
    if not text:
        return None
    try:
        s = text.find("{")
        e = text.rfind("}")
        if s < 0 or e < s:
            return None
        obj = json.loads(text[s:e + 1])
        return {"is_chart": bool(obj.get("is_chart")),
                "has_annotation": bool(obj.get("has_annotation")),
                "note": str(obj.get("note", ""))[:200]}
    except (ValueError, json.JSONDecodeError):
        return None


def resize_for_vision(path):
    """缩小到 MAX_W x MAX_H 内（不放大），转 jpg 写 /tmp。返回临时路径。"""
    img = cv2.imread(path)
    if img is None:
        return None
    h, w = img.shape[:2]
    scale = min(1.0, MAX_W / w, MAX_H / h)
    if scale < 1.0:
        img = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))),
                         interpolation=cv2.INTER_AREA)
    os.makedirs(TMP_DIR, exist_ok=True)
    tmp = os.path.join(TMP_DIR, os.path.splitext(os.path.basename(path))[0] + ".jpg")
    cv2.imwrite(tmp, img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return tmp


def build_messages(prompt, image_path):
    b64 = base64.b64encode(open(image_path, "rb").read()).decode()
    return [{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]}]


def decide(reason, v):
    """决策：no-grid 看 is_chart；no-anno/error 看 has_annotation。"""
    if v is None:
        return "unknown", "视觉读不了，保持排除，待人工确认"
    if reason == "no-grid":
        if v["is_chart"]:
            return "keep", f"视觉判为走势图（{v['note']}），特殊版式，捞回走整图直读"
        return "excluded", f"确认非走势图（{v['note']}）"
    if v["has_annotation"]:
        return "keep", f"视觉发现博主标注（{v['note']}），OpenCV 漏检，捞回"
    return "excluded", f"确认无标注（{v['note']}）"


def main():
    fix_print()
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--limit", type=int, default=0, help="只校验前 N 张（0=全部）")
    ap.add_argument("--include-no-grid", action="store_true",
                    help="默认跳过 no-grid（形态已定非走势图）；加此参数才视觉校验 no-grid")
    args = ap.parse_args()

    out_root = os.path.join(REPO, "data", "recognize", f"{args.date}_all")
    excl_path = os.path.join(out_root, "exclude_list.json")
    excl = load_json(excl_path) or {}
    if not excl.get("excluded"):
        print(f"[verify] 无待校验图: {excl_path}")
        sys.exit(0)
    files = sorted(excl["excluded"].items(), key=lambda kv: -(kv[1].get("size_kb") or 0))
    if not args.include_no_grid:
        # no-grid = 小尺寸/非行网格图，形态上已确定非走势图，无需视觉二次确认，直接剔除
        skipped = [(n, r) for n, r in files if r.get("reason") == "no-grid"]
        files = [(n, r) for n, r in files if r.get("reason") != "no-grid"]
        for n, r in skipped:
            r.setdefault("decision", "excluded")
            r["verify_note"] = "no-grid 小尺寸非走势图，形态判定直接剔除，跳过视觉校验"
        print(f"[verify] 跳过 no-grid {len(skipped)} 张（直接剔除），待视觉校验 {len(files)} 张")
        write_json(excl, excl_path)
    if args.limit:
        files = files[:args.limit]
    img_dir = os.path.join(REPO, "data", "crawl", args.date, "images")

    results = {"date": args.date, "model": VISION_MODEL, "n_total": len(files), "images": {}}
    t0 = time.time()
    n_excl = n_keep = n_unk = 0
    for i, (name, rec) in enumerate(files, 1):
        reason = rec.get("reason", "?")
        src = os.path.join(img_dir, name)
        r = {"file": name, "reason": reason, "size_kb": rec.get("size_kb")}
        if not os.path.exists(src):
            r.update({"decision": "unknown", "note": "源图缺失"})
            decision, note = "unknown", "源图缺失"
        else:
            t1 = time.time()
            print(f"[verify] {i}/{len(files)} 校验中 {name[:46]}（{reason}）", flush=True)
            tmp = resize_for_vision(src)
            if not tmp:
                decision, note = "unknown", "读图失败"
            else:
                content = call_vision(build_messages(PROMPT, tmp))
                v = parse_verify(content)
                r["llm_seconds"] = round(time.time() - t1, 1) if content else None
                if v is not None:
                    r["is_chart"], r["has_annotation"] = v["is_chart"], v["has_annotation"]
                decision, note = decide(reason, v)
        r["decision"], r["note"] = decision, note
        results["images"][name] = r
        if decision == "excluded":
            n_excl += 1
        elif decision == "keep":
            n_keep += 1
        else:
            n_unk += 1
        # 回写 exclude_list 该项
        excl["excluded"][name]["decision"] = decision
        excl["excluded"][name]["verify_note"] = note
        results["_progress"] = {"done": i, "total": len(files), "excluded": n_excl,
                                "keep": n_keep, "unknown": n_unk,
                                "elapsed_s": round(time.time() - t0, 1)}
        write_json(results, os.path.join(out_root, "verify_excluded.json"))
        write_json(excl, excl_path)
        print(f"[verify] {i}/{len(files)} {name[:46]} -> {decision}（{note[:60]}）", flush=True)

    results["_progress"] = {"done": len(files), "total": len(files), "excluded": n_excl,
                            "keep": n_keep, "unknown": n_unk,
                            "elapsed_s": round(time.time() - t0, 1)}
    write_json(results, os.path.join(out_root, "verify_excluded.json"))
    write_json(excl, excl_path)
    print(f"[verify] DONE {len(files)} excluded={n_excl} keep={n_keep} unknown={n_unk} {time.time()-t0:.0f}s")
    print(f"[verify] -> {os.path.join(out_root, 'verify_excluded.json')}")


if __name__ == "__main__":
    main()
