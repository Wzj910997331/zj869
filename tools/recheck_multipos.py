#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多位置重读：对本期 image_patterns_with_blogger.json 中所有 hit 记录对应的图，
GLM 逐张重读，展开博主实际预测的全部位置（博主一张图往往预测 2-4 个位置），
输出 glm_multipos_recheck.json：{说明, actual, hits:[{file,blogger,predicted_positions,pos_check,logic,multi}], rejected:[]}
用法: python tools/recheck_multipos.py --base data/crawl/20260829 --period 26231 \
      --draw "1 8 7 9 9" --calib 26230 --calib-draw "9 4 6 8 3" --workers 6
"""
import argparse
import concurrent.futures
import json
import os
import re
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_URL = "http://llm.riverbegin.cn/v1/chat/completions"
MODEL = "glm-5.3-flash"
POS = ["万位", "千位", "百位", "十位", "个位"]


def load_api_key():
    k = os.environ.get("DEEPSEEK1_API_KEY")
    if k:
        return k
    for p in (os.path.join(REPO, ".credentials.yaml"),
              os.path.expanduser("~/.dsh/.credentials.yaml"),
              os.path.expanduser("~/.claude/.credentials.yaml")):
        if os.path.exists(p):
            try:
                import yaml
                return yaml.safe_load(open(p, encoding="utf-8")).get("DEEPSEEK1_API_KEY")
            except Exception:
                pass
    return None


def call_vision(api_key, image_path, prompt, timeout=60):
    import base64
    import urllib.request
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    mime = "image/jpeg"
    if image_path.lower().endswith(".png"):
        mime = "image/png"
    body = {
        "model": MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64," + b64}},
            ],
        }],
        "temperature": 0.1,
        "max_tokens": 16000,  # 网关"始终思考"型：隐藏 reasoning 吃 token，给足防 content 截断为空
    }
    req = urllib.request.Request(
        API_URL, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
    return data["choices"][0]["message"]["content"]


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


def build_prompt(filename, period, draw, calib, calib_draw):
    return f"""你是图片分析专家。请用 read_image 工具读取图片：{filename}
这是"排列五走势图"（博主手画规律/预测）。真实开奖（用于校准列位）：
- {calib}期 = {calib_draw}（万 千 百 十 个）—— 校准行，最近一期已开奖
- {period}期 = {draw}（万 千 百 十 个）—— 目标期（博主预测的期，本期实际开奖）
博主预测目标期是 {period}（{calib}期之后的第一行/最下一行）。

【任务】找出博主在这张图上实际预测了【哪几个位置】、每个位置的候选数字。
博主一张图往往预测 2~4 个位置，请全部列出（不要只写命中的，也不要只写一个）。

对每个被预测的位置：
- 位置: 万位/千位/百位/十位/个位（用 {calib} 校准行锚定列位：{calib}={calib_draw} 依次对应万/千/百/十/个）
- 候选: 该位置的候选数字（int 数组）
- 标注方式: 博主如何标注（圈/线/框/手写/色块等）
- 原文: 图上写的内容原文（如 "4/9"、"胆3"）

position_check: 对每个预测位置，对照 {period}={draw} 的实际开奖，写"✓"或"✗"。
logic: 用一句话说明博主这条预测的画法/连线/推理逻辑（从图上可见的线、圈、历史数字推导）。
reject_reason: 若这张图其实【没有画规律】（纯数字缩水/报号式推荐/不定位铁码），说明为什么；否则填 null。
multi: 一句话概括，如"4位置1中"、"3位置1中"、"2位置1中"、"1位置1中"、"不定位铁码"、"无画规"。

注意：只报告图中真实可见内容，不猜测。
请只返回一个合法 JSON（不要任何多余文字/代码块）：
{{"file":"{filename}","blogger":"","predicted_positions":[{{"位置":"","候选":[],"标注方式":"","原文":""}}],"position_check":{{}},"logic":"","reject_reason":null,"multi":""}}"""


def adaptive_timeout(path):
    """大图识别更慢，超时随面积递增（与 recognize_patterns.py 同口径）。
    公式: max(60, area//60000)，封顶 180s。裁剪图小 → 基础 60s 足够。"""
    try:
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
            a = w * h
    except Exception:
        a = os.path.getsize(path)
    return min(180, max(60, a // 60000))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="data/crawl/YYYYMMDD")
    ap.add_argument("--period", required=True)
    ap.add_argument("--draw", required=True)
    ap.add_argument("--calib", required=True)
    ap.add_argument("--calib-draw", required=True)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--crops-dir", default=None,
                    help="裁剪产物根目录（如 data/recognize/20260830_all）。命中图优先读该目录下的 "
                         "02_annotated.png（博主标注行栈更聚焦）；读不到再回退原图 images/。")
    args = ap.parse_args()

    api_key = load_api_key()
    if not api_key:
        print("找不到 DEEPSEEK1_API_KEY")
        sys.exit(1)

    BASE = os.path.join(REPO, "data", "crawl", args.base)
    # 裁剪映射：file → 02_annotated.png 绝对路径（命中图优先读裁剪图，更聚焦）
    crop_map = {}
    if args.crops_dir:
        mp = os.path.join(args.crops_dir, "crops_all_manifest.json")
        if os.path.exists(mp):
            for name, rec in json.load(open(mp, encoding="utf-8"))["images"].items():
                if rec.get("status") == "cropped" and rec.get("crop_dir"):
                    crop_map[name] = os.path.join(args.crops_dir, rec["crop_dir"], "02_annotated.png")
            print(f"crops: 已载入 {len(crop_map)} 张裁剪图映射")
        else:
            print(f"警告: 找不到 {mp}，--crops-dir 忽略，回退原图")
    records = json.load(open(os.path.join(BASE, "image_patterns_with_blogger.json"), encoding="utf-8"))
    hits = [r for r in records if r.get("hit") and not (r.get("type") == "杀号" and not r.get("numbers"))]
    # 按图去重（同一张图可能多条命中记录）
    seen, by_file = set(), []
    for r in hits:
        b = os.path.splitext(os.path.basename(r["file"]))[0]
        if b not in seen:
            seen.add(b)
            by_file.append({"file": r["file"], "blogger": r.get("blogger")})
    print(f"命中记录 {len(hits)} 条 / 命中图 {len(by_file)} 张")

    out_path = os.path.join(BASE, "glm_multipos_recheck.json")
    existing = {}
    if args.resume and os.path.exists(out_path):
        rk = json.load(open(out_path, encoding="utf-8"))
        for h in rk.get("hits", []):
            existing[os.path.splitext(os.path.basename(h["file"]))[0]] = h
        print(f"resume: 已有 {len(existing)} 张")

    def one(entry):
        b = os.path.splitext(os.path.basename(entry["file"]))[0]
        if b in existing:
            return existing[b], b
        # 优先裁剪图（博主标注行栈，重读更聚焦）；回退原图
        img = crop_map.get(entry["file"])
        if not img or not os.path.exists(img):
            img = os.path.join(BASE, "images", entry["file"])
            if not os.path.exists(img):
                # 尝试扩展名匹配
                for ext in (".jpg", ".png", ".jpeg"):
                    q = os.path.join(BASE, "images", b + ext)
                    if os.path.exists(q):
                        img = q
                        break
        if not os.path.exists(img):
            return {"file": entry["file"], "blogger": entry["blogger"], "error": "missing"}, b
        for attempt in range(3):
            try:
                raw = call_vision(api_key, img, build_prompt(
                    entry["file"], args.period, args.draw, args.calib, args.calib_draw),
                    timeout=max(120, adaptive_timeout(img)))  # 保底 120s：recheck prompt 复杂，60s 网关慢时易超时
                v = extract_json(raw)
                if v is None:
                    time.sleep(2 + attempt * 2)
                    continue
                # 身份键以数据源文件名为准：裁剪图 basename 全部同名（02_annotated.png），
                # 模型无法从裁剪图得知源文件名，若回填裁剪名会破坏下游 apply 的 file 匹配。
                v["file"] = entry["file"]
                v["blogger"] = entry["blogger"]
                return v, b
            except Exception as e:
                time.sleep(5 * (attempt + 1))
                if attempt == 2:
                    return {"file": entry["file"], "blogger": entry["blogger"], "error": str(e)[:80]}, b
        return {"file": entry["file"], "blogger": entry["blogger"], "error": "empty"}, b

    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        for i, (r, b) in enumerate(ex.map(one, by_file), 1):
            results[b] = r
            if i % 10 == 0:
                print(f"  进度 {i}/{len(by_file)}")
            if r.get("error"):
                print(f"  [err] {r['file']}: {r['error']}")

    hits_out, rejected, errors_out = [], [], []
    for r in results.values():
        if r.get("error"):
            # 失败条目不落盘：带 error 的条目不在 hits/rejected 中，下次 --resume 会重新查询重试
            errors_out.append(r)
        elif r.get("reject_reason"):
            rejected.append({"file": r.get("file"), "blogger": r.get("blogger"),
                             "reason": r["reject_reason"], "multi": r.get("multi", "无画规")})
        else:
            hits_out.append(r)

    out = {
        "说明": f"GLM 多位置重读 {args.period} 期命中图（{MODEL}）",
        "actual": [int(x) for x in args.draw.split()],
        "hits": hits_out,
        "rejected": rejected,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("=" * 40)
    print(f"重读 {len(hits_out)} 张命中图 + {len(rejected)} 张剔除 + {len(errors_out)} 张失败 -> {out_path}")
    print("剔除:", [(r["blogger"], r["multi"]) for r in rejected])


if __name__ == "__main__":
    main()
