#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stage 4：裁剪图 → 视觉模型读数字 + 规则候选 + 模型选择规律

关键发现（实测）：
- 网关 llm.riverbegin.cn 的模型（glm-5.3-flash / deepseek-v4-flash）都是**始终思考**型：
  开放式"从彩票数字找规律 / 从候选里挑选"任务会推理死循环、max_tokens 全被隐藏 reasoning
  吃光、内容为空（12 候选 4000mt、reasoning_effort=low 均试过）。
- 只有**受限小任务**能稳定终止：glm 视觉读数字（10/10 行 5/5 精确）、deepseek 一句话叙事概括（~2s）。
- deepseek-v4-flash 无视觉能力（读图全 null），glm 有。

因此本阶段架构（每图）：
1. **视觉读数字**（glm-5.3-flash，默认）：标注行栈/全行栈 → 逐行读出 5 位数；
   与 lottery_recent 精确匹配自校正 → row→期号映射（row0 锚定目标期）。
2. **规则引擎提候选 + 确定性选 top-3**（无 LLM、精确）：从匹配行提取 斜连/定位/和值/胆码/杀号/
   头/尾/数字串 候选，按支持度 + 博主色带覆盖位置提权排序取前 3。数字全部来自真实开奖，不臆造。
3. **叙事总结**（deepseek-v4-flash，默认，~2s）：对确定的规律做一句话中文概括（大模型规律分析落点）；
   失败则跳过，规律不受影响。
4. **hit() 校验** + 落盘 patterns.json + docs 报告。

用法：
  /usr/bin/python3 modules/image_recognize/stage4_llm.py \
    --manifest data/recognize/<blogger>/<date>/manifest.json \
    [--model glm-5.3-flash] [--analysis-model deepseek-v4-flash] \
    [--mode fast|full] [--timeout 300]
"""
import argparse
import base64
import json
import os
import re
import socket
import sys
import time
import urllib.request

from common import (REPO, load_json, parse_position, write_json, run_hits, fix_print)

BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "http://llm.riverbegin.cn")
AUTH_TOKEN = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
DEFAULT_MODEL = "glm-5.3-flash"
VALID_TYPES = {"定位", "斜连", "胆码", "头", "尾", "和值", "杀号", "数字串", "其他"}


def call_llm(model, messages, max_tokens=3000, timeout=180):
    """调 OpenAI chat/completions。429/5xx 指数退避×3；空输出/网络异常重试×3。
    返回内容字符串（可能为 None）。"""
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
            print(f"[llm] 空输出（推理/截断），重试 {attempt + 1}/4")
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                wait = 10 * (2 ** attempt)
                print(f"[llm] HTTP {e.code}，退避 {wait}s")
                time.sleep(wait)
                continue
            raise
        except socket.timeout as e:
            print(f"[llm] 超时 {timeout}s（{model}），重试 {attempt + 1}/4")
            time.sleep(5)
        except Exception as e:
            print(f"[llm] 网络异常: {e}，重试 {attempt + 1}/4")
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
        # 行内任一数字为 null（模型只读清部分）→ 整行不可靠，防 int(None) 崩溃
        if not isinstance(v, list) or len(v) != 5 or any(x is None for x in v):
            out[i] = {"read": v if isinstance(v, list) else None, "matched": False}
            continue
        out[i] = {"read": [int(x) for x in v]}
    return out


def self_correct(rows_read, lottery, target_period, target_draw):
    """row→period 映射：每行按读数在 lottery 唯一精确匹配，**不预设行序**。

    实测（2026-09-01）：小屁股 / 生活很无奈 的走势图均为"最新在底部"，
    row0 是最旧期（小屁股 row0=26219，生活很无奈 row0≈26217），
    而目标期（最新开奖）位于图最下方、常为预填空行。旧版把 row0 硬锚
    为目标期导致整列错位（映射全是假匹配）。现改为纯读数匹配。

    返回 {row: {"period", "draw", "read", "matched"}}。
    """
    byval = {}
    for p in lottery:
        nums = tuple(int(x) for x in p.get("numbers", []))
        byval.setdefault(nums, []).append(str(p.get("period", "")))
    out = {}
    for i, info in rows_read.items():
        read = info.get("read")
        if not read:
            out[i] = {"period": None, "draw": None, "read": None, "matched": False}
            continue
        if target_draw and read == target_draw:
            out[i] = {"period": target_period, "draw": target_draw,
                      "read": read, "matched": True, "anchor": True}
            continue
        per = byval.get(tuple(read), [])
        if len(per) == 1:
            draw = [int(x) for x in read]
            out[i] = {"period": per[0], "draw": draw, "read": read, "matched": True}
        else:
            out[i] = {"period": None, "draw": None, "read": read, "matched": False,
                      "candidates": per}
    return out


def build_read_prompt(target_period, target_draw, n_rows):
    """视觉读数字 prompt（实测 glm-5.3-flash 3000 max_tokens 精确读 10/10 行）。
    只读数字，不带规律分析（塞在一起会把推理 max_tokens 吃光返回空）。
    注意：不预设 row0 是目标期——实测走势图行序是"最新在底部"（row0 最旧、
    rowN 最新），全部行都要读，由 self_correct 按读数匹配 lottery。"""
    return f"""你是排列5走势图分析师。下面这张图是博主标注过的走势图局部，每行左侧有红色行标签 rowN。
最新一期开奖（参考）：{target_period} = {target_draw}（万/千/百/十/个位）。
图中 row0..row{max(n_rows - 1, 1)} 是各历史期，按原图顺序排列（行标签越大的越靠下）。

任务：逐行精确读出每个数字（万/千/百/十/个 共5个），博主画的色带可能盖住部分数字，透过色带仍可辨认；读不清的行给 null。
只输出一个合法 JSON，格式严格如下（不要任何多余文字/代码块）：
{{"rows": {{"row1": [4,8,2,9,9], "row2": null}}}}
positions 顺序固定为 [万位,千位,百位,十位,个位]。"""


POS_NAMES = ["万", "千", "百", "十", "个"]


def extract_candidates(mapping, anno_pos):
    """确定性规则引擎：从匹配行提取规律候选（支持度排序、标注位置提权）。
    无 LLM、数字全部来自真实开奖。anno_pos: {row: [pos,...]}。
    返回最多 12 条 {type, position, numbers, support, desc[, anno]}。"""
    from collections import Counter
    # 匹配行按期号升序（chronological）排列——不依赖 row 索引方向，
    # 对"最新在顶/在底"两种走势图都成立；seq[-1] 恒为最近期。
    rows = []
    for i, v in mapping.items():
        if v.get("matched") and v.get("draw") and v.get("period"):
            rows.append(v)
    rows.sort(key=lambda v: int(v["period"]))
    cands = []
    n = len(rows)
    if n < 3:
        return cands
    for p in range(5):
        seq = [v["draw"][p] for v in rows]
        # 等差斜连（近3期连续）
        if n >= 3:
            d1, d2 = seq[-1] - seq[-2], seq[-2] - seq[-3]
            if d1 == d2 and d1 != 0:
                cands.append({"type": "斜连", "position": p, "numbers": [seq[-1]],
                              "support": 3,
                              "desc": f"{POS_NAMES[p]}位近3期 {seq[-3]},{seq[-2]},{seq[-1]} 等差 {d1:+d}"})
        # 相邻相连（近2期差±1）
        if n >= 2 and abs(seq[-1] - seq[-2]) == 1:
            cands.append({"type": "斜连", "position": p, "numbers": [seq[-2], seq[-1]],
                          "support": 2, "desc": f"{POS_NAMES[p]}位近2期 {seq[-2]}→{seq[-1]} 相连"})
        # 定位热号
        hot = Counter(seq).most_common(1)[0]
        if hot[1] >= 2:
            cands.append({"type": "定位", "position": p, "numbers": [hot[0]],
                          "support": hot[1], "desc": f"{POS_NAMES[p]}位 {hot[0]} 出现 {hot[1]}/{n} 期"})
    # 和值
    sums = [sum(v["draw"]) for v in rows]
    sc = Counter(sums)
    for s, c in sc.most_common(3):
        if c >= 2:
            cands.append({"type": "和值", "position": None, "numbers": [s],
                          "support": c, "desc": f"和值 {s} 出现 {c}/{n} 期"})
    # 胆码（跨位置高频）
    dc = Counter(d for v in rows for d in v["draw"])
    for d, c in dc.most_common(4):
        if c >= 3:
            cands.append({"type": "胆码", "position": None, "numbers": [d],
                          "support": c, "desc": f"数字 {d} 跨位置出现 {c}/{n*5} 次"})
    # 杀号（近5期未出现）
    recent = rows[-5:]
    present = set(d for v in recent for d in v["draw"])
    absent = [d for d in range(10) if d not in present]
    if absent:
        cands.append({"type": "杀号", "position": None, "numbers": absent,
                      "support": 1, "desc": f"近{len(recent)}期未出现"})
    # 头/尾
    for p, name in ((0, "头"), (4, "尾")):
        c = Counter(v["draw"][p] for v in rows).most_common(1)[0]
        if c[1] >= 2:
            cands.append({"type": name, "position": p, "numbers": [c[0]],
                          "support": c[1], "desc": f"{name}位 {c[0]} 出现 {c[1]} 期"})
    # 数字串（相邻行 2-3 位重叠）
    for i in range(1, n):
        a, b = rows[i - 1]["draw"], rows[i]["draw"]
        for L in (2, 3):
            for j in range(5 - L + 1):
                if a[j:j + L] == b[j:j + L]:
                    cands.append({"type": "数字串", "position": j + L - 1,
                                  "numbers": list(a[j:j + L]), "support": 2,
                                  "desc": f"相邻两期 位置{j+1} 起 {L} 位相同 {''.join(map(str, a[j:j+L]))}"})
    # 去重 + 标注提权 + 排序
    seen, uniq = set(), []
    for c in cands:
        k = (c["type"], c.get("position"), tuple(c["numbers"]))
        if k in seen:
            continue
        seen.add(k)
        uniq.append(c)
    anno_flat = {p for pos in anno_pos.values() for p in pos}
    for c in uniq:
        if c.get("position") in anno_flat:
            c["support"] += 2
            c["anno"] = True
    uniq.sort(key=lambda c: (-c["support"], c["type"]))
    return uniq[:12]


def build_narrative_prompt(target_period, target_draw, patterns, anno_desc):
    """叙事总结 prompt：对已确定的规律做一句话概括（小输入、确定性任务，
    推理模型能终止 ~2s；开放式"找规律"会死循环，所以只在事后做概括）。"""
    lines = [f"{i + 1}) [{p['type']}] {POS_NAMES[p['position']] if p.get('position') is not None else '全位'} "
             f"数字{p['numbers']}：{p.get('desc', '')}"
             for i, p in enumerate(patterns)]
    return f"""以下是程序从某博主走势图标注区识别的规律（数字均来自真实开奖）：
{chr(10).join(lines)}
{anno_desc or ''}
最新一期 {target_period} = {target_draw}。
请用一句话（不超过40字）概括这些规律反映的数字趋势，直接输出这句话，不要推理过程、不要输出JSON。"""


def process_one(manifest, img_name, info, out_dir, model, analysis_model, mode, timeout, lottery):
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

    # Call A：视觉读数字（glm-5.3-flash，已验证精确；deepseek 无视觉能力）
    prompt = build_read_prompt(manifest["target_period"], manifest["target_draw"], n_rows)
    print(f"[stage4] {img_name}: 读数字（{os.path.basename(img_rel)}）...")
    t0 = time.time()
    content = call_llm(model, build_messages(prompt, img_path),
                       max_tokens=16000, timeout=timeout)
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

    # 规则引擎提候选 + 确定性选择 top-3（支持度+标注提权；选择无 LLM，精确不臆造）
    anno_pos = info.get("saturated_positions") or {}
    candidates = extract_candidates(mapping, anno_pos)
    pos_names = ["万", "千", "百", "十", "个"]
    parts = []
    for r, pos in sorted(anno_pos.items(), key=lambda kv: int(kv[0])):
        parts.append(f"row{r} {','.join(pos_names[p] for p in pos)}位")
    anno_desc = "博主色带覆盖行：" + "；".join(parts) if parts else ""

    selected = [{"type": c["type"], "position": c.get("position"),
                 "numbers": c["numbers"], "desc": c.get("desc")}
                for c in candidates[:3]]
    patterns = run_hits(selected, manifest["target_draw"])

    # Call B：叙事总结（deepseek-v4-flash，~2s；开放式规律推导会死循环，只做事后概括）
    analysis_note = None
    if patterns:
        print(f"[stage4] {img_name}: 叙事总结（{analysis_model}）...")
        content = call_llm(analysis_model,
                           [{"role": "user", "content": build_narrative_prompt(
                               manifest["target_period"], manifest["target_draw"],
                               patterns, anno_desc)}],
                           max_tokens=1000, timeout=timeout)
        if content:
            analysis_note = content.strip().strip('"').strip()[:200]
        else:
            print(f"[stage4]   {img_name}: 叙事失败（跳过，规律已落盘）")

    rec = {
        "file": img_name,
        "img_type": "走势图圈选",
        "images_used": [img_rel],
        "n_candidates": len(candidates),
        "analysis_note": analysis_note,
        "rows": {str(i): v for i, v in mapping.items()},
        "annotations": [{"row": r, "positions": pos}
                        for r, pos in sorted(anno_pos.items(), key=lambda kv: int(kv[0]))],
        "patterns": patterns,
        "n_patterns": len(patterns),
        "llm_seconds": round(time.time() - t0, 1),
    }
    return rec


def write_report(manifest, results, out_dir, model, analysis_model):
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
        f"- 视觉读数字模型：{model}　规律选择模型：{analysis_model}",
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
        lines.append(f"- 图类型：{rec['img_type']}　识别耗时 {rec.get('llm_seconds', '?')}s"
                     f"　规则候选 {rec.get('n_candidates', 0)} 条")
        n_ok = sum(1 for v in rec["rows"].values() if v.get("matched"))
        n_tot = len(rec["rows"])
        lines.append(f"- 行读数自校正：{n_ok}/{n_tot} 行匹配 lottery")
        unk = [r for r, v in rec["rows"].items() if not v.get("matched")]
        if unk:
            lines.append(f"- 未匹配行：{unk}（读数不可靠，未参与分析）")
        if rec.get("annotations"):
            lines.append("- 博主色带覆盖（视觉阶段确定性检测）：")
            for a in rec["annotations"]:
                pos = a.get("positions")
                pos_s = ",".join(POS_NAMES[p] for p in pos) if pos else "?"
                lines.append(f"  - {a.get('row')} 行：{pos_s}位")
        lines.append("- 提炼规律（规则候选 top-3 + 模型叙事 → hit 校验）：")
        if not rec["patterns"]:
            lines.append("  - 无")
        for p in rec["patterns"]:
            hit = "✅命中" if p.get("hit") else "未中"
            pos = p.get("position")
            pos_s = f"位置{pos}({POS_NAMES[pos]})" if pos is not None else "全位"
            lines.append(f"  - [{p['type']}] {pos_s} 数字{p['numbers']} {hit}"
                         f"{'　' + p['desc'] if p.get('desc') else ''}")
        if rec.get("analysis_note"):
            lines.append(f"- 模型解读：{rec['analysis_note']}")
        lines.append("")
    lines.append("---")
    lines.append("> 独立模块 image_recognize 自动生成；规律为模型从图中提取，仅供参考。")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def main():
    fix_print()
    ap = argparse.ArgumentParser(description="Stage 4: 视觉大模型读数字 + 规则候选 + 模型选择")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--model", default=DEFAULT_MODEL,
                    help="视觉读数字模型（glm-5.3-flash 已验证；需有视觉能力）")
    ap.add_argument("--analysis-model", default="deepseek-v4-flash",
                    help="叙事总结模型（glm 对规律任务推理死循环，默认 deepseek-v4-flash 快且稳）")
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
        try:
            rec = process_one(manifest, name, info, out_dir, args.model,
                              args.analysis_model, args.mode, args.timeout, lottery)
        except Exception as e:
            # 单图异常隔离：不拖垮整批，其余图照常出结果
            import traceback
            traceback.print_exc()
            rec = {"file": name, "error": f"处理异常: {e}"}
        results[name] = rec
        if rec.get("error"):
            print(f"[stage4]   {name}: 失败 {rec['error']}")
        else:
            n_ok = sum(1 for v in rec["rows"].values() if v.get("matched"))
            print(f"[stage4]   {name}: 行匹配 {n_ok}/{len(rec['rows'])} "
                  f"候选 {rec['n_candidates']} → 规律 {rec['n_patterns']} "
                  f"{rec['llm_seconds']}s")

    out = {"run_id": manifest["run_id"], "blogger": manifest["blogger"],
           "date": manifest["date"], "target_period": manifest["target_period"],
           "target_draw": manifest["target_draw"], "model": args.model,
           "analysis_model": args.analysis_model, "mode": args.mode,
           "images": results}
    out_path = os.path.join(out_dir, "patterns.json")
    write_json(out, out_path)
    report = write_report(manifest, results, out_dir, args.model,
                          args.analysis_model)
    print(f"[stage4] -> {out_path}")
    print(f"[stage4] -> {report}")


if __name__ == "__main__":
    main()
