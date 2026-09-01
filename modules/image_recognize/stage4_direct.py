#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stage4_direct.py — 非绿字走势图直读处理（独立于裁剪管道的第二路逻辑）。

背景：26232 全量裁剪时用绿字过滤误跳了黑/红/蓝字走势图（博主画规律不挑字色）。
对这类图走"整图直读"：整图缩小 → dsv4-vision 直读全部行数字 + 博主标注行 →
self_correct 匹配 lottery → 规则候选 + 叙事 → 落盘。不依赖 stage1 列/行网格检测。

用法：
  /usr/bin/python3 modules/image_recognize/stage4_direct.py \
    --candidates data/recognize/20260830_by_size/candidates_others.json \
    [--max N] [--min-sat 0.03] [--out data/recognize/20260830_others]
"""
import argparse
import json
import os
import sys
import time
import urllib.request

import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stage4_llm import (call_llm, build_messages, parse_json, normalize_rows,
                        self_correct, extract_candidates,
                        build_narrative_prompt, run_hits, POS_NAMES)
from common import REPO, load_json, write_json, fix_print

VISION_MODEL = "deepseek-v4-flash-vision-exp"
ANALYSIS_MODEL = "deepseek-v4-flash"
MAX_W = 1024      # 缩小宽度
MAX_H = 2200      # 缩小高度（超长图截高，避免超长输入）
DATE = "20260830"
VISION_TIMEOUT = 45   # 单次视觉调用限时：可读的图 7-15s 出，>45s 即推理死循环，放弃


def call_direct_vision(model, messages, timeout=VISION_TIMEOUT):
    """有界视觉调用：限时 90s、空输出/网络错误不重试。
    复杂整图会触发推理死循环（max_tokens 全被 reasoning 吃光返回空），
    重试只会把每张图拖到 5-20 分钟。读不出的图直接标记失败跳过。"""
    import socket
    body = json.dumps({"model": model, "messages": messages,
                       "max_tokens": 16000}).encode()
    url = f"{os.environ.get('ANTHROPIC_BASE_URL', 'http://llm.riverbegin.cn')}/v1/chat/completions"
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {os.environ.get('ANTHROPIC_AUTH_TOKEN', '')}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            j = json.loads(r.read())
        return j["choices"][0]["message"].get("content", "")
    except Exception:
        return None


def downscale(path):
    """整图缩小到 MAX_W x MAX_H 内，转 jpg，返回临时路径。"""
    img = cv2.imread(path)
    if img is None:
        return None
    h, w = img.shape[:2]
    scale = min(1.0, MAX_W / w, MAX_H / h)
    if scale < 1.0:
        img = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))),
                         interpolation=cv2.INTER_AREA)
    tmp = path + ".direct.jpg"
    cv2.imwrite(tmp, img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return tmp


def build_direct_prompt(target_period, target_draw):
    """直读 prompt：先分类是否走势图，再逐行读数 + 报告博主标注行。"""
    return f"""你是排列5走势图分析师。这张图可能是彩票走势图。
最新一期开奖（参考）：{target_period} = {target_draw}（万/千/百/十/个位）。

先判断：这是不是走势图（有逐期开奖号码表格，每行5个数字）？
- 不是走势图（杀号表/文字预测/其他）→ 输出 {{"not_chart": true}}
- 是走势图 → 从表格第一行数据开始，逐行精确读出5个数字（万/千/百/十/个位），编号 row1,row2,...；
  博主色带/标注盖住的透过标注仍可辨认；读不清的行给 null；
  另外列出博主画了色带/线/圈标注的行：annotations 数组（每项 {{"row": 行号, "positions": [被覆盖的位置序号0-4]}}，没有则 []）。
只输出一个合法JSON，不要多余文字：
{{"rows": {{"row1": [4,8,2,9,9], "row2": null}}, "annotations": [{{"row": 3, "positions": [0,1,2]}}]}}"""


def parse_annotations(obj, rows_read):
    """模型标注 → anno_pos {row: [pos,...]}，容错。"""
    anno_pos = {}
    for a in obj.get("annotations") or []:
        try:
            r = int(a.get("row"))
            ps = [int(x) for x in a.get("positions", [])]
            ps = [p for p in ps if 0 <= p <= 4]
            if r in rows_read and ps:
                anno_pos[str(r)] = ps
        except (TypeError, ValueError):
            continue
    return anno_pos


def write_report(out_dir, target_period, target_draw, results, model, analysis_model):
    docs_dir = os.path.join(REPO, "docs")
    os.makedirs(docs_dir, exist_ok=True)
    path = os.path.join(docs_dir, f"图片规律识别报告-其他字色-{DATE}.md")
    lines = [
        f"# 图片规律识别报告：非绿字走势图直读（{DATE}）", "",
        f"- 目标期：{target_period} = {target_draw}",
        f"- 视觉读数字模型：{model}　规律选择模型：{analysis_model}",
        f"- 候选图数：{len(results)}", "",
        "## 各图识别结果", "",
    ]
    ok = 0
    for name, rec in results.items():
        if rec.get("status") != "ok":
            continue
        ok += 1
        lines.append(f"### {os.path.basename(name)}")
        lines.append(f"- 图类型：走势图　识别耗时 {rec.get('llm_seconds', '?')}s"
                     f"　规则候选 {rec.get('n_candidates', 0)} 条")
        n_ok = sum(1 for v in rec["rows"].values() if v.get("matched"))
        n_tot = len(rec["rows"])
        lines.append(f"- 行读数自校正：{n_ok}/{n_tot} 行匹配 lottery")
        if rec.get("annotations"):
            lines.append("- 博主标注行（模型报告）：" +
                         ";".join(f"row{a['row']} {','.join(POS_NAMES[p] for p in a['positions'])}位"
                                  for a in rec["annotations"]))
        lines.append("- 提炼规律（规则候选 top-3 → hit 校验）：")
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
    lines.append(f"---\n> 成功处理 {ok}/{len(results)}；非绿字走势图直读自动生成。")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def main():
    fix_print()
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--max", type=int, default=0)
    ap.add_argument("--min-sat", type=float, default=0.0)
    ap.add_argument("--out", default=os.path.join(REPO, "data", "recognize", f"{DATE}_others"))
    args = ap.parse_args()

    data = load_json(args.candidates)
    cands = sorted(data["candidates"], key=lambda r: -r["sat_ratio"])  # 标注重者优先
    if args.min_sat:
        cands = [c for c in cands if c["sat_ratio"] >= args.min_sat]
    if args.max:
        cands = cands[:args.max]
    lottery = load_json(os.path.join(REPO, "data", "crawl", DATE, "lottery_recent.json")) or []
    target_period = str(lottery[0].get("period", ""))
    target_draw = [int(x) for x in lottery[0].get("numbers", [])]
    img_dir = os.path.join(REPO, "data", "crawl", DATE, "images")
    os.makedirs(args.out, exist_ok=True)

    results = {}
    t0 = time.time()
    done = ok = not_chart = fail = 0
    for cand in cands:
        name = cand["file"]
        rec = {"file": name, "size": cand["size"], "sat_ratio": cand["sat_ratio"]}
        src = os.path.join(img_dir, name)
        tmp = downscale(src)
        if not tmp:
            rec["status"] = "read-fail"
            fail += 1
            results[name] = rec
            done += 1
            continue
        t1 = time.time()
        print(f"[direct] {done + 1}/{len(cands)} 读取中 {name}（饱和{cand['sat_ratio']:.1%}）", flush=True)
        content = call_direct_vision(VISION_MODEL,
                                     build_messages(build_direct_prompt(target_period, target_draw), tmp))
        rec["llm_seconds"] = round(time.time() - t1, 1)
        obj = parse_json(content)
        if not obj:
            rec["status"] = "llm-fail"
            fail += 1
        elif obj.get("not_chart"):
            rec["status"] = "not_chart"
            not_chart += 1
        else:
            rows_read = normalize_rows(obj.get("rows"), target_period, target_draw)
            mapping = self_correct(rows_read, lottery, target_period, target_draw)
            anno_pos = parse_annotations(obj, rows_read)
            cand_rules = extract_candidates(mapping, anno_pos)
            selected = [{"type": c["type"], "position": c.get("position"),
                         "numbers": c["numbers"], "desc": c.get("desc")}
                        for c in cand_rules[:3]]
            patterns = run_hits(selected, target_draw)
            analysis_note = None
            if patterns:
                anno_desc = ""
                content2 = call_llm(ANALYSIS_MODEL,
                                    [{"role": "user", "content": build_narrative_prompt(
                                        target_period, target_draw, patterns, anno_desc)}],
                                    max_tokens=1000, timeout=300)
                if content2:
                    analysis_note = content2.strip().strip('"').strip()[:200]
            rec.update({
                "status": "ok", "n_candidates": len(cand_rules),
                "analysis_note": analysis_note,
                "rows": {str(i): v for i, v in mapping.items()},
                "annotations": [{"row": int(r), "positions": ps}
                                for r, ps in sorted(anno_pos.items(), key=lambda kv: int(kv[0]))],
                "patterns": patterns, "n_patterns": len(patterns),
            })
            ok += 1
        results[name] = rec
        done += 1
        if done % 10 == 0:
            out_path = os.path.join(args.out, "patterns_others.json")
            write_json({"date": DATE, "target_period": target_period, "target_draw": target_draw,
                        "model": VISION_MODEL, "analysis_model": ANALYSIS_MODEL,
                        "_progress": {"done": done, "ok": ok, "not_chart": not_chart, "fail": fail},
                        "images": results}, out_path)
        n_ok = sum(1 for v in rec["rows"].values() if v.get("matched")) if rec.get("status") == "ok" else 0
        n_ann = len(rec.get("annotations", []))
        print(f"[direct] {done}/{len(cands)} {name} 饱和{cand['sat_ratio']:.2%} "
              f"-> {rec['status']} 行匹配{n_ok} 标注{n_ann} {rec.get('llm_seconds','?')}s", flush=True)

    out_path = os.path.join(args.out, "patterns_others.json")
    write_json({"date": DATE, "target_period": target_period, "target_draw": target_draw,
                "model": VISION_MODEL, "analysis_model": ANALYSIS_MODEL,
                "_progress": {"done": done, "ok": ok, "not_chart": not_chart, "fail": fail,
                              "elapsed_s": round(time.time() - t0, 1)},
                "images": results}, out_path)
    report = write_report(args.out, target_period, target_draw, results, VISION_MODEL, ANALYSIS_MODEL)
    print(f"[direct] DONE {done}/{len(cands)} ok={ok} not_chart={not_chart} fail={fail} {time.time()-t0:.0f}s")
    print(f"[direct] -> {out_path}")
    print(f"[direct] -> {report}")


if __name__ == "__main__":
    main()
