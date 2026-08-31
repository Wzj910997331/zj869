#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stage 4：裁剪图 → 视觉大模型（glm-5.3-flash）读数字 + 规律分析

流程（每图）：
1. 读 crops_manifest.json，取标注行栈（快路径，默认）或全行栈。
2. 调视觉大模型（OpenAI /v1/chat/completions，base64 图），一次返回 JSON：
   rows（逐行读数） / annotations（博主画了什么） / patterns（提炼规律）/ img_type。
3. 自校正：rows 读数与 lottery_recent 逐期精确匹配 → row→period 映射；
   row0 用已知最新期锚定；无唯一匹配的行标 untrusted。
4. 规律命中：对目标期 draw 跑 hit()，附 blogger/file。
5. 落盘 patterns.json + docs 图片规律识别报告。

用法：
  /usr/bin/python3 modules/image_recognize/stage4_llm.py \
    --manifest data/recognize/<blogger>/<date>/manifest.json \
    [--model glm-5.3-flash] [--mode fast|full] [--timeout 300]
"""
import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.request

from common import (REPO, load_json, parse_position, write_json, run_hits, fix_print)

BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "http://llm.riverbegin.cn")
AUTH_TOKEN = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
DEFAULT_MODEL = "glm-5.3-flash"
VALID_TYPES = {"定位", "斜连", "胆码", "头", "尾", "和值", "杀号", "数字串", "其他"}


def call_llm(model, messages, max_tokens=3000, timeout=300):
    """调 OpenAI chat/completions。429/5xx 指数退避×3。返回内容字符串。"""
    body = json.dumps({"model": model, "messages": messages,
                       "max_tokens": max_tokens}).encode()
    url = f"{BASE_URL}/v1/chat/completions"
    for attempt in range(4):
        req = urllib.request.Request(url, data=body, headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {AUTH_TOKEN}"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                j = json.loads(r.read())
            content = j["choices"][0]["message"].get("content", "")
            if content:
                return content
            print(f"[stage4] 空输出（推理用尽 max_tokens），重试 {attempt + 1}/4")
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                wait = 10 * (2 ** attempt)
                print(f"[stage4] HTTP {e.code}，退避 {wait}s")
                time.sleep(wait)
                continue
            raise
        except Exception as e:
            print(f"[stage4] 网络异常: {e}，重试 {attempt + 1}/4")
            time.sleep(5)
    return None


def build_messages(prompt, image_path):
    """OpenAI 多模态消息（text + image_url base64）。"""
    b64 = base64.b64encode(open(image_path, "rb").read()).decode()
    return [{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url",
         "image_url": {"url": f"data:image/png;base64,{b64}"}},
    ]}]


def parse_json(text):
    """从模型文本中抽出首个平衡 {…} 块并解析。失败返回 None。"""
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    for cand in (m.group(0),):
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            pass
    # 逐字符平衡括号兜底
    s = text[text.index("{"):]
    depth = 0
    for i, ch in enumerate(s):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(s[:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def normalize_rows(raw_rows, target_period, target_draw):
    """rows → {row_idx: {"read": [...], "period": ..., "draw": ..., "matched": bool}}
    row0 强制锚定 target；其余行在 lottery 中精确匹配唯一期。
    """
    out = {}
    for k, v in (raw_rows or {}).items():
        if not re.fullmatch(r"row\d+", str(k)):
            continue
        i = int(k[3:])
        if not isinstance(v, list) or len(v) != 5:
            out[i] = {"read": v if isinstance(v, list) else None, "matched": False}
            continue
        out[i] = {"read": [int(x) for x in v]}
    return out


def self_correct(rows_read, lottery, target_period, target_draw):
    """row→period 映射：row0 锚定最新期；其余行按读数在 lottery 精确匹配。
    返回 {row: {"period", "draw", "read", "matched"}}。"""
    byval = {}
    for p in lottery:
        nums = tuple(int(x) for x in p.get("numbers", []))
        byval.setdefault(nums, []).append(str(p.get("period", "")))
    out = {}
    # row0 锚定始终存在（目标期），即使模型未读取
    out[0] = {"period": target_period, "draw": target_draw,
              "read": target_draw, "matched": True, "anchor": True}
    for i, info in rows_read.items():
        if i == 0:
            continue
        read = info.get("read")
        if target_draw and read == target_draw:
            out[i] = {"period": target_period, "draw": target_draw,
                      "read": read, "matched": True, "anchor": True}
            continue
        if not read:
            out[i] = {"period": None, "draw": None, "read": None, "matched": False}
            continue
        per = byval.get(tuple(read), [])
        if len(per) == 1:
            draw = [int(x) for x in read]
            out[i] = {"period": per[0], "draw": draw, "read": read, "matched": True}
        else:
            out[i] = {"period": None, "draw": None, "read": read, "matched": False,
                      "candidates": per}
    return out


def normalize_patterns(raw_patterns, blogger, file, img_type):
    """把模型 patterns 规整到 schema 记录，跑 hit()。非法条目丢弃并计数。"""
    out = []
    skipped = 0
    for p in raw_patterns or []:
        if not isinstance(p, dict):
            skipped += 1
            continue
        t = str(p.get("type", "")).strip()
        if t not in VALID_TYPES:
            t = "其他"
        nums = p.get("numbers")
        if isinstance(nums, int):
            nums = [nums]
        nums = [int(x) for x in (nums or []) if str(x).isdigit()]
        if not nums:
            skipped += 1
            continue
        pos = parse_position(p.get("position"))
        desc = str(p.get("desc", "")).strip()
        out.append({"blogger": blogger, "file": file, "type": t,
                    "position": pos, "numbers": nums,
                    "desc": desc or None, "img_type": img_type})
    return out, skipped


def build_read_prompt(target_period, target_draw, n_rows):
    """视觉读数字 prompt（实测 glm-5.3-flash 3000 max_tokens 精确读 10/10 行）。
    只读数字，不带规律分析（塞在一起会把推理 max_tokens 吃光返回空）。"""
    return f"""你是排列5走势图分析师。下面这张图是博主标注过的走势图局部，每行左侧有红色行标签 rowN。
最新一期开奖：{target_period} = {target_draw}（万/千/百/十/个位），它就是 row0，位于最上方，无需读取。
图中 row1..row{max(n_rows - 1, 1)} 是更早各期，按原图顺序排列。

任务：逐行精确读出每个数字（万/千/百/十/个 共5个），博主画的色带可能盖住部分数字，透过色带仍可辨认；读不清的行给 null。
只输出一个合法 JSON，格式严格如下（不要任何多余文字/代码块）：
{{"rows": {{"row1": [4,8,2,9,9], "row2": null}}}}
positions 顺序固定为 [万位,千位,百位,十位,个位]。"""


def build_analyze_prompt(target_period, target_draw, rows_text, anno_desc):
    """纯文本规律分析 prompt：数字已由视觉阶段读出，此处只做数字规律推导。
    无图 → 输入小、推理轻、快且不臆造数字。"""
    return f"""你是排列5走势图分析师。博主在小屁股_483847515 的走势图上做了标注，下面是读出的各期开奖数字（万/千/百/十/个）：
{rows_text}

博主标注情况：
{anno_desc or "无标注行"}

最新一期 {target_period} = {target_draw}（这是要预测/分析的目标期）。

任务：从上述数字中提炼博主可能依据的规律。每条 type ∈ 定位/斜连/胆码/头/尾/和值/杀号/数字串/其他，
numbers 必须是上表**真实出现的数字**，desc 用一句话说明依据（例如"万位近5期 9,4,6,8,3 含 4-8 斜连"）。
只输出一个合法 JSON，格式严格如下（不要任何多余文字/代码块）：
{{"patterns": [{{"type": "斜连", "position": 2, "numbers": [4,8], "desc": "百位连续两期 4→8"}}]}}
position 编码：0=万位 1=千位 2=百位 3=十位 4=个位；不限位置的规律 position 给 null。"""


def format_rows_text(mapping):
    """self_correct 后的映射 → 供文本分析的期号-数字表。"""
    lines = []
    for i in sorted(mapping, key=int):
        v = mapping[i]
        if v.get("matched") and v.get("draw"):
            lines.append(f"row{i} {v['period']} = {''.join(str(x) for x in v['draw'])}")
    return "\n".join(lines) or "（无匹配行）"


def process_one(manifest, img_name, info, out_dir, model, mode, timeout, lottery):
    stem = os.path.splitext(img_name)[0]
    crops_dir = os.path.join(out_dir, "crops", stem)
    if mode == "full":
        img_rel = info.get("full_rows_file")
    else:
        img_rel = info.get("annotated_file")
    img_path = os.path.join(out_dir, img_rel)
    if not os.path.exists(img_path):
        print(f"[stage4] 缺图片（{mode}）: {img_path}，改用全行栈")
        img_path = os.path.join(out_dir, info.get("full_rows_file", ""))
    n_rows = len(info.get("filled_rows", []))

    # Call A：视觉读数字（已验证可靠）
    prompt = build_read_prompt(manifest["target_period"], manifest["target_draw"], n_rows)
    print(f"[stage4] {img_name}: 读数字（{os.path.basename(img_rel)}）...")
    t0 = time.time()
    content = call_llm(model, build_messages(prompt, img_path),
                       max_tokens=3000, timeout=timeout)
    if not content:
        return {"file": img_name, "error": "LLM 读数字失败", "images_used": [img_rel]}
    obj = parse_json(content)
    if obj is None:
        return {"file": img_name, "error": "读数字 JSON 解析失败",
                "raw": content[:800], "images_used": [img_rel]}
    rows_read = normalize_rows(obj.get("rows"), manifest["target_period"],
                               manifest["target_draw"])
    mapping = self_correct(rows_read, lottery, manifest["target_period"],
                           manifest["target_draw"])
    rows_text = format_rows_text(mapping)

    # Call B：纯文本规律分析（无图，快）
    pos_names = ["万", "千", "百", "十", "个"]
    parts = []
    for r, pos in sorted((info.get("saturated_positions") or {}).items(),
                         key=lambda kv: int(kv[0])):
        parts.append(f"row{r} {','.join(pos_names[p] for p in pos)}位")
    anno_desc = "博主色带覆盖行：" + "；".join(parts) if parts else ""
    print(f"[stage4] {img_name}: 规律分析（文本）...")
    content = call_llm(model,
                       [{"role": "user", "content": build_analyze_prompt(
                           manifest["target_period"], manifest["target_draw"],
                           rows_text, anno_desc)}],
                       max_tokens=2000, timeout=timeout)
    if not content:
        return {"file": img_name, "error": "LLM 规律分析失败", "images_used": [img_rel]}
    obj2 = parse_json(content)
    if obj2 is None:
        return {"file": img_name, "error": "规律分析 JSON 解析失败",
                "raw": content[:800], "images_used": [img_rel]}

    img_type = "走势图圈选"
    patterns, skipped = normalize_patterns(
        obj2.get("patterns"), manifest["blogger"], img_name, img_type)
    rec = {
        "file": img_name,
        "img_type": img_type,
        "images_used": [img_rel],
        "rows": {str(i): v for i, v in mapping.items()},
        "annotations": [{"row": r, "positions": pos}
                        for r, pos in sorted((info.get("saturated_positions") or {}).items(),
                                             key=lambda kv: int(kv[0]))],
        "patterns": run_hits(patterns, manifest["target_draw"]),
        "n_patterns": len(patterns),
        "skipped_invalid": skipped,
        "llm_seconds": round(time.time() - t0, 1),
    }
    return rec


def write_report(manifest, results, out_dir, model):
    """docs 报告（镜像 summarize_image_patterns 风格）。"""
    blog = manifest["blogger"]
    date = manifest["date"]
    docs_dir = os.path.join(REPO, "docs")
    os.makedirs(docs_dir, exist_ok=True)
    path = os.path.join(docs_dir, f"图片规律识别报告-{blog}-{date}.md")
    lines = [
        f"# 图片规律识别报告：{blog}（{date}）",
        "",
        f"- 目标期：{manifest['target_period']} = {manifest['target_draw']}",
        f"- 视觉模型：{model}",
        f"- 图片数：{len(manifest['images'])}",
        "",
        "## 各图识别结果",
        "",
    ]
    for name in manifest["images"]:
        rec = results.get(name)
        if not rec:
            continue
        lines.append(f"### {os.path.basename(name)}")
        if rec.get("error"):
            lines.append(f"- ⚠️ 失败：{rec['error']}")
            lines.append("")
            continue
        lines.append(f"- 图类型：{rec['img_type']}　LLM 耗时 {rec.get('llm_seconds', '?')}s")
        n_ok = sum(1 for v in rec["rows"].values() if v.get("matched"))
        n_tot = len(rec["rows"])
        lines.append(f"- 行读数自校正：{n_ok}/{n_tot} 行匹配 lottery")
        unk = [r for r, v in rec["rows"].items() if not v.get("matched")]
        if unk:
            lines.append(f"- 未匹配行：{unk}（读数不可靠，未参与分析）")
        if rec.get("annotations"):
            pos_names = ["万", "千", "百", "十", "个"]
            lines.append("- 博主色带覆盖（视觉阶段确定性检测）：")
            for a in rec["annotations"]:
                pos = a.get("positions")
                pos_s = ",".join(pos_names[p] for p in pos) if pos else "?"
                lines.append(f"  - {a.get('row')} 行：{pos_s}位")
        lines.append("- 提炼规律：")
        if not rec["patterns"]:
            lines.append("  - 无")
        for p in rec["patterns"]:
            hit = "✅命中" if p.get("hit") else "未中"
            pos = p.get("position")
            pos_s = f"位置{pos}({['万','千','百','十','个'][pos]})" if pos is not None else "全位"
            lines.append(f"  - [{p['type']}] {pos_s} 数字{p['numbers']} {hit}"
                         f"{'　' + p['desc'] if p.get('desc') else ''}")
        lines.append("")
    lines.append("---")
    lines.append("> 独立模块 image_recognize 自动生成；规律为模型从图中提取，仅供参考。")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def main():
    fix_print()
    ap = argparse.ArgumentParser(description="Stage 4: 视觉大模型读图 + 规律分析")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--mode", choices=["fast", "full"], default="fast",
                    help="fast=仅标注行栈（快）；full=全行栈（准）")
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args()

    manifest = load_json(args.manifest)
    if not manifest:
        print("[stage4] ERROR: 读不到 manifest", args.manifest)
        sys.exit(2)
    if not AUTH_TOKEN:
        print("[stage4] ERROR: 未设置 ANTHROPIC_AUTH_TOKEN")
        sys.exit(2)
    out_dir = manifest["out_dir"]
    crops_path = os.path.join(out_dir, "crops_manifest.json")
    crops = load_json(crops_path)
    if not crops:
        print(f"[stage4] ERROR: 读不到 {crops_path}（先跑 stage2）")
        sys.exit(2)
    lottery = load_json(manifest["lottery_path"]) or []

    results = {}
    for name in manifest["images"]:
        base = os.path.basename(name)
        info = crops["images"].get(base) or crops["images"].get(name)
        if not info:
            results[name] = {"file": name, "error": "无裁剪信息"}
            continue
        rec = process_one(manifest, name, info, out_dir, args.model,
                          args.mode, args.timeout, lottery)
        results[name] = rec
        if rec.get("error"):
            print(f"[stage4]   {name}: 失败 {rec['error']}")
        else:
            n_ok = sum(1 for v in rec["rows"].values() if v.get("matched"))
            print(f"[stage4]   {name}: 行匹配 {n_ok}/{len(rec['rows'])} "
                  f"规律 {rec['n_patterns']}（跳过非法 {rec['skipped_invalid']}）"
                  f" {rec['llm_seconds']}s")

    out = {"run_id": manifest["run_id"], "blogger": manifest["blogger"],
           "date": manifest["date"], "target_period": manifest["target_period"],
           "target_draw": manifest["target_draw"], "model": args.model,
           "mode": args.mode, "images": results}
    out_path = os.path.join(out_dir, "patterns.json")
    write_json(out, out_path)
    report = write_report(manifest, results, out_dir, args.model)
    print(f"[stage4] -> {out_path}")
    print(f"[stage4] -> {report}")


if __name__ == "__main__":
    main()
