#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
judge_accuracy.py — ④ 视觉模型判定"博主预测是否命中本期开奖" + 规律分析(流程改造第四步)。

四步管线最后一步: 对 filter_trend 判定 keep/uncertain 的图, 读已 resize 的裁剪栈图(≥1024 宽),
模型读博主标注行的数字 → 确定性对齐(期→行) → 确定性标注位检测 → 逐位 hit/miss → 抽规律。

2026-09-01 冒烟两轮实测结论(驱动本次重写):
  1. 640 宽栈图浅色数字读不清 → resize 已改为上采样到 ≥1024 宽; 放大后 ds 33s 读全 8 标注行。
  2. 模型"读标注位置+透过标注的数字"子任务触发网关"始终思考"死循环(ds/glm 双超时);
     但"只读行数字"是稳定小任务(ds 6-33s)。故 prompt 只问行数字, 标注位置/数字全部确定性判定。
  3. 标注位置确定性检测(本模块实现): 灰度<thr 掩码全域投影 → 宽峰(剔高密度块/窄网格线) → 5 列;
     每行饱和 run 中心 → 最近列 → 位(0=万..4=个)。两图实测与人工核验一致。
  4. 标注数字 = 该行该位读数(博主色带盖在历史数字上) → 命中判定为纯算术, 零模型成本。

每图流水线:
  vision/{stem}.jpg(≥1024 栈图)
    → Pass1 ds(deepseek-v4-flash-vision-exp): build_judge_prompt 只读标注行数字(无标注子任务)
    → normalize_rows + self_correct_safe + validate_alignment(A-G, import analyze_crops_ds)
    → deterministic_annotations: {标注行 → [位0-4]} (灰度投影列 + 饱和run)
    → 确定性判定: 博主预测数字 X 命中本期 p 位 ⟺ X == target_draw[p]
    → ds 过且 ≥1 标注行有位置+读数 → ds-ok; 否则(--glm-fallback 开时) Pass2 glm 兜底
  规律: extract_candidates(mapping, anno_pos) + run_hits(draw) —— 与 analyze_crops_ds 同口径

模型约束(全程 ds, glm 仅兜底): call_ds_vision(max_tokens=16000, 有界单次, 网关断连抛 DsConnError)。

输出(新文件, 不覆盖任何现有产物):
  data/recognize/{date}_all/analysis/judge_{date}.json      每图 {decision, checks, rows, annotations, verdict, patterns, llm_seconds}
  data/crawl/{date}/predictions_with_blogger.json           博主归属扁平记录(patterns + annotations 两段)

用法:
  /usr/bin/python3 modules/image_recognize/judge_accuracy.py \
    --date 20260831 --target-period 26233 --draw "1 6 3 4 0" \
    --manifest data/recognize/20260831_all/crops_all_manifest.json \
    --filter data/crawl/20260831/filter_report.json \
    --vision data/recognize/20260831_all/vision_manifest.json \
    --lottery data/crawl/20260831/lottery_recent.json \
    --posts data/crawl/20260831/posts.json [--src-dir data/crawl/20260831/images] \
    [--files "s_2_x.jpg,s_2_y.jpg" | --limit N] [--glm-fallback] [--workers 2]
"""
import argparse
import base64
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from common import (REPO, load_json, write_json, fix_print, run_hits,  # noqa: E402
                    normalize_blogger, POS_NAMES)
from stage4_llm import call_llm, parse_json, normalize_rows, extract_candidates  # noqa: E402
from analyze_crops_ds import (call_ds_vision, self_correct_safe,  # noqa: E402
                              validate_alignment, blogger_of_file, DsConnError)
from crop_all import process_one, saturation_mask  # noqa: E402

DS_MODEL = "deepseek-v4-flash-vision-exp"
GLM_MODEL = "glm-5.3-flash"
POS_CHARS = ["万", "千", "百", "十", "个"]


def keep_or_uncertain(decision):
    """v2/v3 决策名: keep-high / keep-med / uncertain(period-weak|anno-weak)。"""
    return decision.startswith("keep") or decision.startswith("uncertain")

# ---- 确定性标注位置检测 -------------------------------------------------

GRAY_THR = 205        # 灰度掩码阈值: 抓浅色数字(浅蓝/粉/绿), 白底被排除
COL_WIDTH_MIN = 8      # 投影宽峰最小宽度(剔 <8px 窄网格线)
COL_VCAP = 0.6         # 峰高上限(剔 >0.6 高密度块/面板/行标签)
PEAK_TH = 0.12         # 归一化投影峰阈值
RUN_GAP = 5            # 峰合并间隔
RUN_TOL = 32           # run 中心 → 列容差(px)
SAT_MIN = 20           # 行内饱和投影最低峰值
FURN_RATIO = 0.9       # 出现在 ≥90% 行的 run = 图表家具(期号列/行标签), 剔除


def detect_columns(img, info, grid):
    """灰度<thr 掩码全域投影 → 宽峰(剔高密度块/窄网格线) → 选最等距的 5 列。

    返回 5 个列中心(升序)或 None。排列5 走势图各位置异色, 单色掩码必然漏列;
    用灰度掩码捕获全部字迹, 竖网格线(窄)、左侧面板/色块(高密度)被剔除,
    数字列(宽 20-80px、峰高 0.12-0.55)保留。列间距近似等距(实测 pitch 103-120)。
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(int)
    m = (gray < GRAY_THR).astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    rows = grid.get("rows") or []
    x0, x1 = info.get("grid_x", [0, img.shape[1]])[0], info.get("grid_x", [0, img.shape[1]])[1]
    rh = info.get("row_half", 68)
    if not rows:
        return None
    y0 = max(0, rows[0] - rh)
    y1 = min(m.shape[0], rows[-1] + rh)
    v = m[y0:y1, x0:x1].sum(axis=0).astype(float)
    if v.max() <= 0:
        return None
    vn = v / v.max()
    idx = np.where(vn > PEAK_TH)[0]
    runs, prev = [], -99
    for x in idx:
        if x - prev > RUN_GAP:
            runs.append([x, x])
        else:
            runs[-1][1] = x
        prev = x
    cands = []
    for a, b in runs:
        w = b - a + 1
        if w < COL_WIDTH_MIN:
            continue
        pk = (a + b) // 2
        if vn[pk] > COL_VCAP:
            continue
        cands.append(x0 + pk)
    cands = sorted(set(int(c) for c in cands))
    if not cands:
        return None
    if len(cands) < 5:
        if len(cands) >= 2:
            P = int(np.median([cands[i + 1] - cands[i] for i in range(len(cands) - 1)]))
        else:
            P = 105
        while len(cands) < 5:
            if cands[0] - P >= 0:
                cands = [cands[0] - P] + cands
            else:
                cands = cands + [cands[-1] + P]
        return [int(c) for c in cands[:5]]
    from itertools import combinations
    best = None
    for idxs in combinations(range(len(cands)), 5):
        cs = [cands[i] for i in idxs]
        diffs = [cs[i + 1] - cs[i] for i in range(4)]
        sc = max(diffs) - min(diffs)
        if best is None or sc < best[0]:
            best = (sc, cs)
    return [int(c) for c in best[1]]


def deterministic_annotations(img, info, grid):
    """返回 {标注行: [位0-4]}。饱和 run 中心 → 最近列(容差 RUN_TOL) → 位。
    排除出现在 ≥FURN_RATIO 行的家具 run(期号列/行标签, 非博主标注)。"""
    cols = detect_columns(img, info, grid)
    if not cols:
        return {}, None
    b0, g0, r0 = (img[..., 0].astype(int), img[..., 1].astype(int), img[..., 2].astype(int))
    sat = saturation_mask(b0, g0, r0)
    rows = grid.get("rows") or []
    x0, x1 = info.get("grid_x", [0, img.shape[1]])[0], info.get("grid_x", [0, img.shape[1]])[1]
    rh = info.get("row_half", 68)
    n = len(rows)
    rowruns = {}
    for i, y in enumerate(rows):
        y = int(y)
        y0, y1 = max(0, y - rh), min(img.shape[0], y + rh)
        vv = sat[y0:y1, x0:x1].sum(axis=0).astype(float)
        if vv.max() <= SAT_MIN:
            rowruns[i] = []
            continue
        ix = np.where(vv > 0.2 * vv.max())[0]
        runs, prev = [], -99
        for x in ix:
            if x - prev > 15:
                runs.append([x, x])
            else:
                runs[-1][1] = x
            prev = x
        rowruns[i] = [x0 + (a + b) // 2 for a, b in runs if b - a + 1 >= 3]
    from collections import Counter
    cnt = Counter()
    for i in range(n):
        for c in set(rowruns.get(i, [])):
            cnt[c] += 1
    furn = {c for c, k in cnt.items() if k >= FURN_RATIO * n}
    out = {}
    for i in info.get("annotated_rows") or []:
        rc = [c for c in rowruns.get(i, []) if c not in furn]
        pos = sorted(set(
            min(range(5), key=lambda k: abs(cols[k] - c))
            for c in rc if min(abs(cols[k] - c) for k in range(5)) <= RUN_TOL))
        if pos:
            out[i] = pos
    return out, cols


# ---- prompt 与校验 ------------------------------------------------------


def build_msgs(prompt, image_path):
    """OpenAI 多模态消息, mime 按扩展名(与 stage4_llm.build_messages 同, 修 jpg mime)。"""
    b64 = base64.b64encode(open(image_path, "rb").read()).decode()
    ext = os.path.splitext(image_path)[1].lower().lstrip(".") or "png"
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else "image/png"
    return [{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}]}]


def build_judge_prompt(target_period, target_draw, anno_rows):
    """判定 prompt: 纯数字读取, 零标注语义。

    ⚠️ 2026-09-01 冒烟实测: 网关模型全是"始终思考"型, prompt 里一旦出现"标注/透过标注/
    圈选色带"等字样, 模型会启动"识别博主预测标注"子任务 → 推理死循环吃光 max_tokens
    返回空(ds 45-75s 超时, 概率性)。删掉一切标注措辞、只保留"读数字+忽略彩色标记",
    才能稳定落到受限小任务(ds 6-33s 可读)。标注位置/数字全部由确定性
    deterministic_annotations 判定, 命中是纯算术。模型只承担行数字视觉读取。"""
    rows_s = " ".join(f"row{r}" for r in anno_rows)
    return f"""你是走势图数字识别器。图中每行左侧有红色 row 标签。图上可能有彩色标记, 一律忽略, 只读底层数字。
只读这些行的数字: {rows_s}。
每行从左到右恰有 5 个数字(0-9)。某行数字若被标记挡住无法辨认, 整行给 null。
不要思考, 不要解释, 只输出一个 JSON:
{{"rows": {{"row3": [4,8,2,9,9], "row7": null}}}}"""


def deterministic_hits(posmap, mapping, target_draw):
    """确定性命中判定: 每标注行, 位 P 的数字 = 该行读数[P], 命中 ⟺ == target_draw[P]。
    返回 (per_anno, flags)。flags 非空 = 该行读数与该位标注不一致(读数不可信, 供 glm 兜底)。"""
    target = [int(x) for x in target_draw]
    per_anno, flags = [], []
    for row in sorted(posmap):
        rec = {"row": row, "positions": posmap[row]}
        row_read = mapping.get(str(row)) or mapping.get(row)
        if not row_read or row_read.get("read") is None:
            rec["row_valid"] = False
            flags.append(f"row{row} 标注行无读数")
            per_anno.append(rec)
            continue
        rec["row_valid"] = True
        read = row_read["read"]
        hit_truth = {}
        for p in posmap[row]:
            x = read[p] if p < len(read) else None
            if x is None:
                flags.append(f"row{row}位{POS_CHARS[p]}读数缺失")
            hit_truth[POS_CHARS[p]] = (x == target[p]) if p < 5 and x is not None else None
        rec["hit_truth"] = hit_truth
        rec["hit"] = any(v for v in hit_truth.values())
        per_anno.append(rec)
    return per_anno, flags


def vision_pass(img, rec, target_period, target_draw, lottery, timeout, max_tokens, is_glm):
    """单次视觉读取(只读标注行数字) + 自校正 + 校验 + 确定性标注位/命中。返回 dict。"""
    anno_rows = sorted(rec.get("annotated_rows") or [])
    prompt = build_judge_prompt(target_period, target_draw, anno_rows)
    msgs = build_msgs(prompt, img)
    t0 = time.time()
    try:
        if is_glm:
            content = call_llm(GLM_MODEL, msgs, max_tokens=max_tokens, timeout=timeout)
        else:
            content = call_ds_vision(msgs, timeout=timeout, max_tokens=max_tokens)
    except DsConnError:
        raise
    seconds = round(time.time() - t0, 1)
    if not content:
        return {"ok": False, "error": "调用返回空", "seconds": seconds}
    obj = parse_json(content)
    if obj is None:
        return {"ok": False, "error": "JSON 解析失败", "seconds": seconds, "raw": content[:300]}
    rows_read = normalize_rows(obj.get("rows"), target_period, target_draw)
    mapping = self_correct_safe(rows_read, lottery, target_period, target_draw)
    val = validate_alignment(mapping, rec, target_period)
    # G 门软处理(2026-09-02): 底部标注行"完全没读出"(read=None)时 G 无据可判——
    # "没读到"不是"读错", 不判 fail, 防 ds 读行不全时误伤真实对齐(如 image1 row11)。
    # 若底部标注行已读出但匹配不上(读数错)则仍 fail(期序偏移是真问题)。
    anno = sorted(rec.get("annotated_rows") or [])
    if "G" in val["hard_failures"] and anno:
        bm = mapping.get(anno[-1])
        if bm is not None and bm.get("read") is None:
            val["hard_failures"] = [h for h in val["hard_failures"] if h != "G"]
            val["pass"] = not val["hard_failures"]
    src = rec.get("_src_path")
    posmap, cols = {}, None
    if src and os.path.exists(src):
        try:
            st, pinfo, grid = process_one(src)
            if grid:
                posmap, cols = deterministic_annotations(load_rgb(src), pinfo, grid)
        except Exception as e:
            posmap, cols = {}, None
            print(f"    [det] 确定性标注位失败: {str(e)[:120]}", flush=True)
    per_anno, flags = deterministic_hits(posmap, mapping, target_draw)
    if not per_anno:
        flags.append("无确定性标注位(列检测/饱和run失败)")
    return {"ok": True, "obj": obj, "rows_read": rows_read, "mapping": mapping,
            "val": val, "posmap": posmap, "cols": cols,
            "per_anno": per_anno, "flags": flags, "seconds": seconds}


def load_rgb(path):
    """中文路径安全读图。"""
    data = np.fromfile(path, np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"无法解码: {path}")
    return img


def judge_one(file, rec, resized, lottery, target_period, target_draw, args):
    """单图: ds → (glm) → 决策。返回结果 dict。"""
    if not os.path.exists(resized):
        return {"file": file, "decision": "error", "error": f"缺 resize 图: {resized}"}

    # Pass1 ds(空返回/读行不足 均重试 2 次; ds 单次有界, 不同于 glm 60s×4)
    r, ds_attempts = None, 0
    while ds_attempts < 3:
        try:
            r = vision_pass(resized, rec, target_period, target_draw, lottery,
                            args.timeout_ds, args.max_tokens, is_glm=False)
        except DsConnError as e:
            return {"file": file, "decision": "error", "error": f"网关断连: {e}"}
        ds_attempts += 1
        if r["ok"] and r["val"]["pass"]:
            break
        # 重试条件: 空返回/超时(模型循环) 或 B 门 fail(读行不足, 模型读不全)
        b_fail = "B" in (r.get("val") or {}).get("hard_failures", [])
        if (not r["ok"] or b_fail) and ds_attempts < 3:
            print(f"    [judge] {file} ds 空返回/读行不足, 重试 {ds_attempts}/2", flush=True)
            continue
        break
    if r["ok"] and r["val"]["pass"]:
        return _finalize(file, "ds-ok", "ds", r, target_draw, ds_attempts=ds_attempts)

    ds_fail = {"ok": r.get("ok"), "hard_failures": (r.get("val") or {}).get("hard_failures"),
               "flags": r.get("flags"), "error": r.get("error")}
    # ds 判模型不可信/校验不过 → glm 兜底(默认关闭, glm 对栈图 60-180s 慢)
    if not args.glm_fallback:
        return {"file": file, "decision": "unresolved", "model": "ds",
                "checks": (r.get("val") or {}).get("checks"),
                "metrics": (r.get("val") or {}).get("metrics"),
                "rows": {str(i): {k: m.get(k) for k in ("period", "draw", "read", "matched")}
                         for i, m in (r.get("mapping") or {}).items()},
                "annotations": r.get("per_anno", []), "patterns": [], "n_candidates": 0,
                "llm_seconds": r.get("seconds"), "ds_attempts": ds_attempts, "glm_attempts": 0,
                "hard_failures": (r.get("val") or {}).get("hard_failures"),
                "flags": r.get("flags"), "ds_fail": ds_fail}
    try:
        g = vision_pass(resized, rec, target_period, target_draw, lottery,
                        args.timeout_glm, args.max_tokens, is_glm=True)
    except DsConnError as e:
        return {"file": file, "decision": "error", "error": f"网关断连(glm): {e}"}
    if g["ok"] and g["val"]["pass"]:
        return _finalize(file, "glm-rescue", "glm", g, target_draw, ds_attempts=ds_attempts)
    return {"file": file, "decision": "unresolved", "model": "glm",
            "checks": (g.get("val") or {}).get("checks"),
            "metrics": (g.get("val") or {}).get("metrics"),
            "rows": {str(i): {k: m.get(k) for k in ("period", "draw", "read", "matched")}
                     for i, m in (g.get("mapping") or {}).items()},
            "annotations": g.get("per_anno", []), "patterns": [], "n_candidates": 0,
            "llm_seconds": g.get("seconds"), "ds_attempts": ds_attempts, "glm_attempts": 1,
            "hard_failures": (g.get("val") or {}).get("hard_failures"),
            "flags": g.get("flags"), "ds_fail": ds_fail}


def _finalize(file, decision, model, r, target_draw, ds_attempts=1, glm_attempts=0):
    """过校验 → 提规律候选 + hit 标注, 组装结果。"""
    mapping = r["mapping"]
    anno_pos = {row: posmap for row, posmap in (r.get("posmap") or {}).items()}
    candidates = extract_candidates(mapping, anno_pos)
    cands = [{"type": c["type"], "position": c.get("position"),
              "numbers": c["numbers"], "desc": c.get("desc")} for c in candidates]
    patterns = run_hits(cands, target_draw) if target_draw else [dict(c, hit=None) for c in cands]
    matched_periods = sorted(int(m["period"]) for m in mapping.values() if m.get("matched"))
    return {"file": file, "decision": decision, "model": model,
            "checks": r["val"]["checks"], "metrics": r["val"]["metrics"], "hard_failures": [],
            "matched_periods": matched_periods,
            "cols": r.get("cols"),
            "rows": {str(i): {k: m.get(k) for k in ("period", "draw", "read", "matched")}
                     for i, m in mapping.items()},
            "annotations": r.get("per_anno", []),
            "patterns": patterns, "n_candidates": len(patterns),
            "llm_seconds": r.get("seconds"), "ds_attempts": ds_attempts,
            "glm_attempts": glm_attempts}


def main():
    fix_print()
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--target-period", required=True)
    ap.add_argument("--draw", default=None, help="空格分隔 5 位; 缺省取 lottery[0]")
    ap.add_argument("--manifest", required=True, help="crops_all_manifest.json")
    ap.add_argument("--filter", required=True, help="filter_report.json")
    ap.add_argument("--vision", required=True, help="vision_manifest.json")
    ap.add_argument("--lottery", required=True)
    ap.add_argument("--posts", default=None, help="posts.json(博主归属)")
    ap.add_argument("--src-dir", default=None, help="源图目录(缺省 data/crawl/{date}/images)")
    ap.add_argument("--timeout-ds", type=int, default=75)
    ap.add_argument("--timeout-glm", type=int, default=60)
    ap.add_argument("--max-tokens", type=int, default=16000)
    ap.add_argument("--glm-fallback", action="store_true")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--files", default=None, help="逗号分隔指定源图(冒烟用)")
    ap.add_argument("--out", default=None, help="judge json 输出路径")
    args = ap.parse_args()

    manifest = load_json(args.manifest) or {}
    fr = load_json(args.filter) or {}
    vision = load_json(args.vision) or {}
    lottery = load_json(args.lottery) or []
    if not manifest.get("images") or not fr.get("images") or not vision.get("images"):
        print("[judge] ERROR: 读不到 manifest/filter/vision")
        sys.exit(2)
    target_draw = [int(x) for x in args.draw.split()] if args.draw else (
        list(lottery[0].get("numbers") or []) if lottery else None)
    if not target_draw:
        print("[judge] WARN: 无 target_draw → hit 为 null")

    src_dir = args.src_dir or os.path.join(REPO, "data", "crawl", args.date, "images")

    posts = load_json(args.posts) if args.posts else None
    posts_by_id = {p.get("id"): p for p in posts} if posts else {}

    vimgs = vision["images"]
    todo = [f for f, r in fr.get("images", {}).items()
            if keep_or_uncertain(r.get("decision")) and manifest["images"].get(f, {}).get("status") == "cropped"]
    if args.files:
        want = {f.strip() for f in args.files.split(",") if f.strip()}
        todo = [f for f in todo if f in want]
    elif args.limit:
        todo = todo[:args.limit]
    if not todo:
        print("[judge] 无可判定图")
    for f in todo:
        if f not in vimgs:
            print(f"[judge] WARN 无 resize: {f}")

    out_root = os.path.dirname(os.path.abspath(args.manifest))
    analysis_dir = os.path.join(out_root, "analysis")
    os.makedirs(analysis_dir, exist_ok=True)
    out_path = args.out or os.path.join(analysis_dir, f"judge_{args.date}.json")

    results, t0 = {}, time.time()
    for n, f in enumerate(todo, 1):
        rec = dict(manifest["images"][f])
        rec["_src_path"] = os.path.join(src_dir, f)
        resized = vimgs.get(f, {}).get("resized_path", "")
        if resized and not os.path.isabs(resized):
            resized = os.path.join(REPO, resized)
        if not resized or not os.path.exists(resized):
            results[f] = {"file": f, "decision": "error", "error": "缺 resize 图"}
        else:
            results[f] = judge_one(f, rec, resized, lottery, args.target_period,
                                   target_draw, args)
        dec = results[f]["decision"]
        print(f"[judge] {n}/{len(todo)} {f} -> {dec} "
              f"匹配{len(results[f].get('matched_periods') or [])}期 "
              f"标注{len(results[f].get('annotations') or [])} "
              f"规律{results[f].get('n_candidates', 0)} {results[f].get('llm_seconds')}s", flush=True)
        if n % 5 == 0 or n == len(todo):
            write_json({"date": args.date, "target_period": args.target_period,
                        "target_draw": target_draw, "n_images": len(todo),
                        "_progress": {"done": n, "total": len(todo),
                                      "elapsed_s": round(time.time() - t0, 1)},
                        "images": results}, out_path)

    dist = {}
    for r in results.values():
        dist[r.get("decision", "?")] = dist.get(r.get("decision", "?"), 0) + 1
    judge_report = {"date": args.date, "target_period": args.target_period,
                    "target_draw": target_draw, "n_images": len(todo),
                    "decision_dist": dist, "images": results}
    write_json(judge_report, out_path)

    # 扁平博主归属记录
    flat_patterns, flat_annos = [], []
    for f, r in results.items():
        if r.get("decision") not in ("ds-ok", "glm-rescue"):
            continue
        blogger = blogger_of_file(f, posts_by_id)
        for p in r.get("patterns") or []:
            if p["type"] == "数字串":
                continue
            pos = p.get("position")
            pos_s = POS_CHARS[pos] if isinstance(pos, int) and 0 <= pos <= 4 else (
                pos if isinstance(pos, str) else None)
            flat_patterns.append({"blogger": blogger, "file": f, "type": p["type"],
                                  "position": pos_s, "numbers": p["numbers"],
                                  "desc": p.get("desc", ""), "hit": p.get("hit")})
        for a in r.get("annotations") or []:
            flat_annos.append({"blogger": blogger, "file": f, "row": a.get("row"),
                               "positions": a.get("positions"), "digits": a.get("digits"),
                               "hit_truth": a.get("hit_truth"), "hit": a.get("hit")})
    pred_path = os.path.join(REPO, "data", "crawl", args.date, "predictions_with_blogger.json")
    write_json({"date": args.date, "target_period": args.target_period,
                "target_draw": target_draw, "generated_by": "judge_accuracy.py",
                "patterns": flat_patterns, "annotations": flat_annos}, pred_path)
    print(f"[judge] DONE {len(todo)} 张 {time.time()-t0:.0f}s 决策分布: {dist}")
    print(f"[judge] -> {out_path}")
    print(f"[judge] -> {pred_path}")


if __name__ == "__main__":
    main()
