#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyze_crops_ds.py — 26233 期规律分析：ds 快检 + 确定性校验层 + glm 兜底。

问题：deepseek-v4-flash-vision-exp（ds）快（~7s/图）但可能"对不齐往期开奖"→ 误检。
策略（2026-09-01 用户定案：本期全 ds）= ds 全量快检，逐图通过 A-G 校验层把关；
校验不过（ds 重读 1 次仍不过）→ decision=unresolved 剔出下游并报告，宁缺毋滥。
glm-5.3-flash 兜底默认关闭（对栈图 60-180s 太慢），需时 --glm-fallback 开启。

校验层 validate_alignment（确定性；硬=触发 upgrade，软=仅记录）：
  A 无虚构行  模型编造不存在的行号 = 没按栈结构读（容错 1 个行号看错）        HARD
  B match_ratio  matched / 有有效读数 ≥ 0.6（垃圾读数 0/N → 升级）          HARD
  C 无重复期   matched period 无重复（一图一期）                             HARD
  D 单调性     matched period 随行序严格递增/递减（≥3 行；期序错乱=误读撞期） HARD
  E 时效       max(matched) ≥ target−5（防整张读到远古期）                   HARD
  F 标注覆盖   匹配标注行 ≥ max(3, 0.5×n_annotated)（部分行误读为正常容错）   SOFT
  G 底部对齐   底部标注区：inc → max∈{target,target−1}；dec → 顶部∈{target,target−1}  HARD

注：行号只是 key，期对齐靠"数字精确匹配 lottery"。行号错标但数字读对 → 期序仍正确
（D/E/G 验证）；真正错位只在数字误读撞到错误期时发生。F 作为软指标避免过度升级。

默认全 ds：ds 重读 1 次仍不过校验 → decision=unresolved（剔出下游+报告，宁缺毋滥）。
glm 兜底默认关闭（glm 对栈图要 60-180s 太慢），需要时 --glm-fallback 开启。

复用 stage4_llm 纯函数；self_correct 在本文件内联"去 anchor 特判"版本
（不修改共享 stage4_llm.py，避免影响其他 agent）。

用法：
  /usr/bin/python3 modules/image_recognize/analyze_crops_ds.py \
    --manifest data/recognize/20260831_all/crops_all_manifest.json \
    --date 20260831 --target-period 26233 \
    --lottery data/crawl/20260831/lottery_recent.json \
    [--target-draw "1 6 3 4 0"] [--limit 10] [--resume] ...
"""
import argparse
import json
import os
import random
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from common import (REPO, load_json, write_json, fix_print, run_hits, normalize_blogger)
from stage4_llm import (call_llm, build_messages, parse_json, normalize_rows,
                        extract_candidates, build_read_prompt, build_narrative_prompt)

DS_MODEL = "deepseek-v4-flash-vision-exp"
GLM_MODEL = "glm-5.3-flash"
ANALYSIS_MODEL = "deepseek-v4-flash"
POS_CHARS = ["万", "千", "百", "十", "个"]
BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "http://llm.riverbegin.cn")
AUTH_TOKEN = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")


class DsConnError(Exception):
    """网关断连（瞬时，resume 应重跑该图，而非定为 unresolved）。"""


def call_ds_vision(messages, timeout=45, max_tokens=16000):
    """ds 有界单次调用（"始终思考"型，max_tokens 必须 ≥16000；卡死时空返回）。
    失败/空 → None，不重试（重试只会把每张图拖到 5-20 分钟）。
    网关断连（Connection refused/reset 等）→ 抛 DsConnError，resume 重跑该图。"""
    body = json.dumps({"model": DS_MODEL, "messages": messages,
                       "max_tokens": max_tokens}).encode()
    req = urllib.request.Request(f"{BASE_URL}/v1/chat/completions", data=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {AUTH_TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            j = json.loads(r.read())
        return j["choices"][0]["message"].get("content", "")
    except Exception as e:
        msg = str(e)
        reason = getattr(e, "reason", None)
        conn_types = (ConnectionRefusedError, ConnectionResetError, BrokenPipeError,
                      ConnectionAbortedError)
        # 注意：socket.timeout/TimeoutError 不算连接失败——超时是模型真慢，属于真实失败
        is_conn = (isinstance(reason, conn_types) or
                   isinstance(e, OSError) and getattr(e, "errno", None) in (104, 111, 32, 54) or
                   "Connection refused" in msg or "Connection reset" in msg or
                   "Remote end closed connection" in msg or "Connection aborted" in msg)
        if is_conn:
            raise DsConnError(msg[:120])
        print(f"    [ds] 调用失败: {msg[:80]}")
        return None


PROVIDER_MODEL = {"ds": DS_MODEL, "glm": GLM_MODEL}


def _chat_once(model, messages, timeout, max_tokens):
    """单次有界 OpenAI chat/completions（**不重试**）。返回 (content, None) 或 (None, 失败类)。

    失败类（供上层决定是否换 provider）：
      conn   = 网关断连/过载(429/5xx/refused/reset) → 换家可救
      timeout= 模型真慢/"始终思考"死循环 → 换家可救
      empty  = 空返/非网络异常 → 换家多半仍空，只记一次
    """
    body = json.dumps({"model": model, "messages": messages,
                       "max_tokens": max_tokens}).encode()
    req = urllib.request.Request(f"{BASE_URL}/v1/chat/completions", data=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {AUTH_TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            j = json.loads(r.read())
        content = j["choices"][0]["message"].get("content", "")
        return (content if content else None), (None if content else "empty")
    except Exception as e:
        msg = str(e)
        reason = getattr(e, "reason", None)
        conn_types = (ConnectionRefusedError, ConnectionResetError, BrokenPipeError,
                      ConnectionAbortedError)
        is_conn = (isinstance(reason, conn_types) or
                   isinstance(e, OSError) and getattr(e, "errno", None) in (104, 111, 32, 54) or
                   "Connection refused" in msg or "Connection reset" in msg or
                   "Remote end closed connection" in msg or "Connection aborted" in msg)
        if is_conn:
            return None, "conn"
        if isinstance(e, (TimeoutError, socket.timeout)) or "timed out" in msg.lower():
            return None, "timeout"
        if isinstance(e, urllib.error.HTTPError) and e.code in (429, 500, 502, 503, 504):
            return None, "conn"
        return None, "empty"


def call_vision_auto(messages, providers=("glm", "ds"), timeout=90, max_tokens=16000,
                     verbose=True):
    """视觉模型自动切换：每家 provider **单次有界**调用（绝不重试，防"始终思考"死循环拖 5-20 分钟）。

    每次调用失败按 _chat_once 归类（conn=网关断连/timeout=模型超时/empty=空返）→ 换下一个 provider。
    providers 顺序 = 优先顺序。默认 ("glm","ds")：glm 布局/位读更准优先，ds 兜底（2026-09-02 用户要求
    "超时后判断是否 AI 网关超时，超时就换 ds 或 glm"）。
    全部失败 → 返回 (None, 最后一个 provider)。
    """
    if isinstance(providers, str):
        providers = tuple(p.strip() for p in providers.split(",") if p.strip())
    if not providers:
        providers = ("glm", "ds")
    trace = []
    for i, name in enumerate(providers):
        model = PROVIDER_MODEL.get(name.strip().lower())
        if not model:
            continue
        content, fail = _chat_once(model, messages, timeout, max_tokens)
        if content:
            if verbose:
                print(f"    [vision-auto] {name}({model}) 成功，{timeout}s 上限内")
            return content, name.strip().lower()
        trace.append(f"{name}:{fail}")
        if verbose:
            nxt = providers[i + 1] if i + 1 < len(providers) else "无家可换"
            print(f"    [vision-auto] {name}({model}) 失败[{fail}/{timeout}s] → {nxt}")
    if verbose:
        print(f"    [vision-auto] 全部失败: {'; '.join(trace)}")
    return None, providers[-1].strip().lower()


def self_correct_safe(rows_read, lottery, target_period, target_draw):
    """同 stage4_llm.self_correct，但去掉 anchor 特判：任何读数==target_draw 的行
    直接标目标期（不查是否底部行）会让"历史重复开出 target 同号"的行被误标本期，
    正是"对不齐往期"误检来源。byval 池含 target_draw 时目标行本可正常匹配。
    返回 {row: {"period","draw","read","matched"}}。"""
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
        per = byval.get(tuple(read), [])
        if len(per) == 1:
            out[i] = {"period": per[0], "draw": [int(x) for x in read],
                      "read": read, "matched": True}
        else:
            out[i] = {"period": None, "draw": None, "read": read, "matched": False,
                      "candidates": per}
    return out


def validate_alignment(mapping, rec, target_period, K=5, match_ratio_min=0.6):
    """A-G 确定性校验（2026-09-01 冒烟校准版）。mapping: {row_idx: {period,draw,read,matched}}。
    rec: crops_all_manifest.images[file]（annotated_rows/n_rows）。

    设计原则：行号只是 key，期对齐靠"数字精确匹配 lottery"。行号错标但数字读对 →
    期序仍正确（D/E/G 已验证）；真正错位只在数字误读撞到错误期时发生
    （D 非单调 / G 底部锚 / C 重复）。因此硬集只留"错位/垃圾"信号，部分误读、漏读为软指标。

    硬（触发 glm 升级）：A 无虚构行（容错 1） B match_ratio C 无重复期 D 单调 E 时效 G 底部对齐
    软（仅记录不升级）：F 标注覆盖（≥max(3, 0.5×n_annotated)）
    返回 {"pass", "checks":{A..G:{pass,hard,detail}}, "metrics":{...},
          "hard_failures":[...], "direction":...}。"""
    target = int(target_period)
    anno = sorted(rec.get("annotated_rows") or [])
    bottom = anno[-1] if anno else -1
    n_rows = rec.get("n_rows") or (bottom + 1)

    reported = set(mapping.keys())
    expected = set(anno)
    invented = reported - expected
    missing = expected - reported
    checks = {}

    # A 无虚构行（硬）：模型编造不存在的行号 = 没按栈结构读，数字可信度存疑。
    # 容错 1 个（栈标签小、个别看错行号但数字读对时，期序仍由 D/E/G 验证）。
    a_ok = len(invented) <= 1
    checks["A"] = {"pass": a_ok, "hard": True,
                   "detail": f"虚构{len(invented)}{f' 行{sorted(invented)}' if invented else ''} "
                             f"漏{len(missing)}"}

    # B match_ratio（硬）：有有效读数行中的匹配率 ≥ 0.6；且必须尝试读 ≥50% 标注行
    # （防"懒读"——只读好读的 2-3 行全匹配就假装过；垃圾读数 0/N 也在此拦截）。
    valid = [r for r, m in mapping.items() if m.get("read") is not None]
    matched = [r for r, m in mapping.items() if m.get("matched")]
    ratio = len(matched) / len(valid) if valid else 0.0
    need_attempt = max(3, int(0.5 * len(anno))) if anno else 0
    attempt_ok = len(valid) >= need_attempt
    checks["B"] = {"pass": (ratio >= match_ratio_min) and attempt_ok, "hard": True,
                   "detail": f"{len(matched)}/{len(valid)} matched，读数 {len(valid)}/{len(anno)}"
                             f"（需≥{need_attempt}）"}

    # C 无重复期（硬）：一图一期（lottery 期唯一，重复=同一读数读两遍/误读撞期）。
    pers = [m.get("period") for m in mapping.values() if m.get("matched")]
    dup = len(pers) != len(set(pers))
    checks["C"] = {"pass": not dup, "hard": True, "detail": f"matched {len(pers)} 期"}

    # D 单调性（硬）：matched 期随行序严格递增/递减（走势图"最新在底部"→ 通常 inc）。
    # 期序非单调 = 数字误读撞到错位期或行序错乱，直接升级。
    rows_ordered = sorted((r, m) for r, m in mapping.items() if m.get("matched"))
    ps = [int(m["period"]) for _, m in rows_ordered]
    direction = None
    if len(ps) >= 3:
        inc = all(b > a for a, b in zip(ps, ps[1:]))
        dec = all(b < a for a, b in zip(ps, ps[1:]))
        direction = "inc" if inc else ("dec" if dec else None)
        checks["D"] = {"pass": inc or dec, "hard": True,
                       "detail": f"方向={direction} 期序{ps[:4]}{'…' if len(ps)>4 else ''}"}
    else:
        checks["D"] = {"pass": True, "hard": True, "detail": f"matched<3（{len(ps)}）跳过"}

    # E 时效（硬）：最新 matched 期 ≥ target−K（防整张读到远古期/偏移整段）。
    maxp = max(ps) if ps else None
    checks["E"] = {"pass": maxp is not None and maxp >= target - K, "hard": True,
                   "detail": f"最新期={maxp}（≥{target - K}）"}

    # F 标注覆盖（软）：匹配上的标注行 ≥ max(3, 0.5×n_annotated)。部分行数字误读 → 未匹配，
    # 属正常容错，不升级；期对齐由 B/D/E/G 把关。
    anno_matched = [r for r in anno if mapping.get(r, {}).get("matched")]
    f_need = max(3, int(0.5 * len(anno))) if anno else 0
    checks["F"] = {"pass": len(anno_matched) >= f_need, "hard": False,
                   "detail": f"标注匹配 {len(anno_matched)}/{len(anno)}（需≥{f_need}）"}

    # G 底部对齐（硬，外部锚，防"ds 一致性偏移"）：底部标注区（最新行带）触发。
    # inc（最新在底部）：max(matched) ∈ {target, target−1}；dec（最新在顶）：min 同理。
    minp = min(ps) if ps else None
    g_ok, g_detail = True, "跳过（非底部标注/方向未知/无匹配）"
    if bottom >= n_rows - 2 and maxp is not None:
        if direction == "inc":
            g_ok = maxp in (target, target - 1)
            g_detail = f"底部标注且 inc：最新期={maxp}，需∈{{{target},{target - 1}}}"
        elif direction == "dec":
            g_ok = minp in (target, target - 1)
            g_detail = f"底部标注且 dec：最旧期={minp}（底部）→ 顶部={maxp}，需顶部∈{{{target},{target - 1}}}"
    checks["G"] = {"pass": g_ok, "hard": True, "detail": g_detail}

    hard_failures = [c for c, v in checks.items() if v.get("hard") and not v["pass"]]
    metrics = {"direction": direction, "max_period": maxp, "min_period": minp,
               "recency_gap": (target - maxp) if maxp is not None else None,
               "n_matched": len(matched), "n_valid": len(valid),
               "n_annotated": len(anno), "dup_periods": dup}
    return {"pass": not hard_failures, "checks": checks, "metrics": metrics,
            "hard_failures": hard_failures, "direction": direction}


def read_stack(out_root, rec):
    """标注行栈路径 + prompt 行数（max 标注行 +1，修正 stage4_llm 的 filled_rows 口径）。"""
    img = os.path.join(out_root, rec.get("crop_dir", ""), "02_annotated.png")
    n_rows = max(rec.get("annotated_rows") or [0]) + 1
    return img, n_rows


def run_vision_pass(img, prompt_n_rows, rec, model, target_period, target_draw,
                    lottery, timeout, max_tokens, is_glm):
    """单次视觉读取 → 自校正 → 校验。返回 {"ok", "mapping", "val", "seconds", "error"}。"""
    draw_s = " ".join(str(x) for x in target_draw) if target_draw else "? ? ? ? ?"
    prompt = build_read_prompt(target_period, draw_s, prompt_n_rows)
    msgs = build_messages(prompt, img)
    t0 = time.time()
    if is_glm:
        content = call_llm(model, msgs, max_tokens=max_tokens, timeout=timeout)
    else:
        content = call_ds_vision(msgs, timeout=timeout, max_tokens=max_tokens)
    seconds = round(time.time() - t0, 1)
    if not content:
        return {"ok": False, "error": f"{model} 调用返回空", "seconds": seconds}
    obj = parse_json(content)
    if obj is None:
        return {"ok": False, "error": "JSON 解析失败", "seconds": seconds,
                "raw": content[:300]}
    rows_read = normalize_rows(obj.get("rows"), target_period, target_draw)
    mapping = self_correct_safe(rows_read, lottery, target_period, target_draw)
    val = validate_alignment(mapping, rec, target_period)
    return {"ok": True, "mapping": mapping, "val": val, "seconds": seconds}


def finalize_patterns(mapping, target_draw):
    """从通过校验的 mapping 提规律候选（确定性，数字全来自真实开奖）→ hit 标注。
    返回 (patterns, matched_periods, row_map)。"""
    candidates = extract_candidates(mapping, {})  # crop_all 无标注位信息，anno_pos={}
    cands = [{"type": c["type"], "position": c.get("position"),
              "numbers": c["numbers"], "desc": c.get("desc")} for c in candidates]
    if target_draw:
        patterns = run_hits(cands, target_draw)
    else:
        patterns = [dict(c, hit=None) for c in cands]
    matched_periods = sorted(int(m["period"]) for m in mapping.values() if m.get("matched"))
    row_map = {str(i): {k: m.get(k) for k in ("period", "draw", "read", "matched")}
               for i, m in mapping.items()}
    return patterns, matched_periods, row_map


def phase1_ds(file, rec, out_root, lottery, target_period, target_draw, args):
    """Pass1：ds 快检（有界单次 + 可选重读 1 次）。ds 过 → ds-ok；不过 → phase=1 中间态。"""
    img, n_rows = read_stack(out_root, rec)
    if not os.path.exists(img):
        return {"file": file, "decision": "error", "error": "缺 02_annotated.png"}
    attempts = 1 + max(0, args.ds_retries)
    last = None
    for i in range(attempts):
        try:
            r = run_vision_pass(img, n_rows, rec, args.ds_model, target_period, target_draw,
                                lottery, args.timeout_ds, args.max_tokens, is_glm=False)
        except DsConnError as e:
            # 网关断连：瞬时可重跑，不定 unresolved（resume 会重跑 decision=error 的图）
            return {"file": file, "decision": "error", "error": f"网关断连: {e}"}
        if r["ok"] and r["val"]["pass"]:
            patterns, mps, row_map = finalize_patterns(r["mapping"], target_draw)
            return {"file": file, "decision": "ds-ok", "model": "ds",
                    "checks": r["val"]["checks"], "metrics": r["val"]["metrics"],
                    "hard_failures": [],
                    "matched_periods": mps, "rows": row_map,
                    "patterns": patterns, "n_candidates": len(patterns),
                    "llm_seconds": r["seconds"], "ds_attempts": i + 1, "glm_attempts": 0}
        last = r
    return {"file": file, "phase": "1",
            "ds": {"ok": False, "attempts": attempts,
                   "val": last.get("val") if last else None,
                   "error": last.get("error") if last else "读取失败",
                   "seconds": last.get("seconds") if last else None}}


def phase2_glm(file, rec, out_root, lottery, target_period, target_draw, args, phase1):
    """Pass2：glm 兜底读同一栈，同款校验。过 → glm-rescue；不过 → unresolved。"""
    img, n_rows = read_stack(out_root, rec)
    r = run_vision_pass(img, n_rows, rec, args.glm_model, target_period, target_draw,
                        lottery, args.timeout_glm, args.max_tokens, is_glm=True)
    ds_fail = {"attempts": phase1.get("ds", {}).get("attempts"),
               "hard_failures": (phase1.get("ds", {}).get("val") or {}).get("hard_failures"),
               "error": phase1.get("ds", {}).get("error")}
    if r["ok"] and r["val"]["pass"]:
        patterns, mps, row_map = finalize_patterns(r["mapping"], target_draw)
        return {"file": file, "decision": "glm-rescue", "model": "glm",
                "checks": r["val"]["checks"], "metrics": r["val"]["metrics"],
                "hard_failures": [],
                "matched_periods": mps, "rows": row_map,
                "patterns": patterns, "n_candidates": len(patterns),
                "llm_seconds": r["seconds"], "glm_attempts": 1,
                "ds_attempts": phase1.get("ds", {}).get("attempts", 0),
                "ds_fail": ds_fail}
    return {"file": file, "decision": "unresolved", "model": "glm",
            "checks": (r.get("val") or {}).get("checks"),
            "metrics": (r.get("val") or {}).get("metrics"),
            "patterns": [], "n_candidates": 0, "llm_seconds": r.get("seconds"),
            "error": r.get("error"),
            "hard_failures": (r.get("val") or {}).get("hard_failures"),
            "ds_attempts": phase1.get("ds", {}).get("attempts", 0), "glm_attempts": 1,
            "ds_fail": ds_fail}


def add_narrative(rec, target_period, target_draw, timeout=300):
    """对已定案且有规律的单图做一句话叙事概括（deepseek-v4-flash ~2s）。"""
    if rec.get("decision") not in ("ds-ok", "glm-rescue") or not rec.get("patterns"):
        return rec
    content = call_llm(ANALYSIS_MODEL,
                       [{"role": "user", "content": build_narrative_prompt(
                           target_period, target_draw, rec["patterns"], "")}],
                       max_tokens=1000, timeout=timeout)
    rec["analysis_note"] = (content or "").strip().strip('"').strip()[:200] if content else None
    return rec


def blogger_of_file(file, posts_by_id):
    m = re.match(r"^(s_2_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})_", file)
    if not m:
        return "未知"
    nm = (posts_by_id.get(m.group(1)) or {}).get("creator", {}).get("name", "")
    return normalize_blogger(nm) or "未知"


def build_flat(recs, keep_digit_run):
    """汇总成下游 schema image_patterns_with_blogger.json：仅 ds-ok/glm-rescue。"""
    out = []
    for f, rec in recs.items():
        if rec.get("decision") not in ("ds-ok", "glm-rescue"):
            continue
        for p in rec.get("patterns") or []:
            if not keep_digit_run and p["type"] == "数字串":
                continue
            pos = p.get("position")
            pos_s = POS_CHARS[pos] if isinstance(pos, int) and 0 <= pos <= 4 else (
                pos if isinstance(pos, str) else None)
            out.append({"blogger": rec.get("blogger", ""), "file": f,
                        "type": p["type"], "position": pos_s,
                        "numbers": p["numbers"], "desc": p.get("desc", ""),
                        "img_type": "走势图圈选", "hit": p.get("hit")})
    return out


def build_report(results, files, target_period, target_draw, args):
    report = {"date": args.date, "target_period": target_period,
              "target_draw": target_draw, "n_images": len(files),
              "thresholds": {"K": 5, "match_ratio_min": 0.6, "ds_retries": args.ds_retries},
              "decision_dist": {}, "checks": {}, "unresolved": []}
    for c in "ABCDEFG":
        report["checks"][c] = {"pass": 0, "fail": 0, "samples": []}
    for f in files:
        rec = results.get(f) or {}
        dec = rec.get("decision") or "pending"
        report["decision_dist"][dec] = report["decision_dist"].get(dec, 0) + 1
        for c, v in (rec.get("checks") or {}).items():
            if c not in report["checks"]:
                continue
            if v["pass"]:
                report["checks"][c]["pass"] += 1
            else:
                report["checks"][c]["fail"] += 1
                if len(report["checks"][c]["samples"]) < 5:
                    report["checks"][c]["samples"].append(
                        {"file": f[:50], "detail": v["detail"]})
        if dec == "unresolved":
            report["unresolved"].append({"file": f, "blogger": rec.get("blogger"),
                                         "hard_failures": rec.get("hard_failures"),
                                         "error": rec.get("error"),
                                         "ds_fail": rec.get("ds_fail")})
    return report


def write_docs(report, results, out_root, target_period, target_draw):
    path = os.path.join(out_root, "analysis", f"validate_report_{target_period}.md")
    lines = [f"# 26233 期规律分析校验报告", "",
             f"- 目标期：{target_period} = {target_draw}",
             f"- 图片数：{report['n_images']}", ""]
    lines.append(f"- 决策分布：{report['decision_dist']}")
    lines.append("")
    lines.append("## 各校验项通过/失败")
    for c, v in report["checks"].items():
        lines.append(f"- {c}: pass {v['pass']} / fail {v['fail']}")
        for s in v["samples"]:
            lines.append(f"    - {s['file']}: {s['detail']}")
    lines.append("")
    lines.append("## unresolved（ds 校验不过，已剔除，待人工复核）")
    if not report["unresolved"]:
        lines.append("- 无")
    for u in report["unresolved"]:
        df = u.get("ds_fail") or {}
        lines.append(f"- {u['file']} ({u['blogger']}) 失败项={u.get('hard_failures')} "
                     f"原因={u.get('error') or df.get('error') or '校验不过'}")
    lines.append("")
    lines.append(f"> analyze_crops_ds.py 自动生成，阈值 K=5 / match_ratio_min=0.6。")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def main():
    fix_print()
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--date", default="20260831")
    ap.add_argument("--target-period", required=True)
    ap.add_argument("--target-draw", default=None, help="空格分隔 5 位；缺省取 lottery[0]")
    ap.add_argument("--lottery", required=True)
    ap.add_argument("--ds-model", default=DS_MODEL)
    ap.add_argument("--glm-model", default=GLM_MODEL)
    ap.add_argument("--workers-ds", type=int, default=4)
    ap.add_argument("--workers-glm", type=int, default=3)
    ap.add_argument("--timeout-ds", type=int, default=45)
    ap.add_argument("--timeout-glm", type=int, default=60)
    ap.add_argument("--max-tokens", type=int, default=16000)
    ap.add_argument("--save-every", type=int, default=20)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--no-narrative", action="store_true", help="跳过叙事概括（加速）")
    ap.add_argument("--no-flat", action="store_true",
                    help="不写 image_patterns_with_blogger.json（冒烟测试用）")
    ap.add_argument("--keep-digit-run", action="store_true", help="汇总保留'数字串'类型")
    ap.add_argument("--ds-retries", type=int, default=1)
    ap.add_argument("--glm-fallback", action="store_true",
                    help="开启 glm 兜底（慢，默认关闭；开启后 ds-fail → glm 重读）")
    ap.add_argument("--glm-sample-rate", type=float, default=0.0,
                    help="对 ds-ok 图抽 %用 glm 复核（一致率报告，0=关）")
    ap.add_argument("--out", default=None, help="分析结果 json 路径")
    args = ap.parse_args()

    manifest = load_json(args.manifest)
    if not manifest or not manifest.get("images"):
        print("[analyze] ERROR: 读不到 manifest 或无 images")
        sys.exit(2)
    lottery = load_json(args.lottery) or []
    if not lottery:
        print("[analyze] ERROR: 读不到 lottery")
        sys.exit(2)
    target_period = args.target_period
    if args.target_draw:
        target_draw = [int(x) for x in args.target_draw.split()]
    else:
        target_draw = list(lottery[0].get("numbers") or []) if lottery else None
    if not target_draw:
        print("[analyze] WARN: 无 target_draw → hit 标记将为 null")

    posts = load_json(os.path.join(REPO, "data", "crawl", args.date, "posts.json")) or []
    posts_by_id = {p.get("id"): p for p in posts if p.get("id")}

    out_root = os.path.dirname(os.path.abspath(args.manifest))
    analysis_dir = os.path.join(out_root, "analysis")
    os.makedirs(analysis_dir, exist_ok=True)
    out_path = args.out or os.path.join(analysis_dir, f"analyze_{args.date}.json")

    cropped = sorted(n for n, r in manifest["images"].items() if r.get("status") == "cropped")
    if args.limit:
        cropped = cropped[args.offset:args.offset + args.limit]
    elif args.offset:
        cropped = cropped[args.offset:]

    results = {}
    if args.resume and os.path.exists(out_path):
        prev = load_json(out_path) or {}
        results = prev.get("images", {}) or {}
    # 待跑：无 decision、decision=error（网关断连可重跑）、或 phase1 但 ds 调用失败（val=None）
    todo = [f for f in cropped
            if f not in results or not results[f].get("decision")
            or results[f].get("decision") == "error"
            or (f in results and results[f].get("phase") == "1"
                and (results[f].get("ds") or {}).get("val") is None)]
    phase1_todo = [f for f in todo
                   if f not in results
                   or results[f].get("phase") != "1"
                   or (results[f].get("ds") or {}).get("val") is None]
    to_phase2 = [f for f in todo if f in results and results[f].get("phase") == "1"
                 and (results[f].get("ds") or {}).get("val") is not None]
    print(f"[analyze] cropped={len(cropped)} 待处理={len(todo)} "
          f"→ phase1 {len(phase1_todo)} / phase2(glm) {len(to_phase2)}", flush=True)

    t0 = time.time()
    if phase1_todo:
        done = 0
        with ThreadPoolExecutor(max_workers=args.workers_ds) as ex:
            futs = {ex.submit(phase1_ds, f, manifest["images"][f], out_root, lottery,
                              target_period, target_draw, args): f for f in phase1_todo}
            for fu in as_completed(futs):
                f = futs[fu]
                try:
                    rec = fu.result()
                except Exception as e:
                    import traceback; traceback.print_exc()
                    rec = {"file": f, "decision": "error", "error": f"异常: {e}"}
                rec.setdefault("blogger", blogger_of_file(f, posts_by_id))
                results[f] = rec
                done += 1
                tag = rec.get("decision") or "ds-fail"
                if done % args.save_every == 0:
                    write_json({"date": args.date, "target_period": target_period,
                                "target_draw": target_draw, "images": results}, out_path)
                print(f"[pass1] {done}/{len(phase1_todo)} {f[:44]} -> {tag}", flush=True)
        write_json({"date": args.date, "target_period": target_period,
                    "target_draw": target_draw, "images": results}, out_path)
        print(f"[pass1] DONE {len(phase1_todo)} 张 {time.time()-t0:.0f}s", flush=True)

    to_phase2 = [f for f in results if results[f].get("phase") == "1"]
    if to_phase2 and not args.glm_fallback:
        # 全 ds 模式：ds 重读后仍不过校验 → 直接定 unresolved，剔出下游（宁缺毋滥）
        for f in to_phase2:
            ph = results[f].get("ds") or {}
            results[f] = {"file": f, "decision": "unresolved", "model": "ds",
                          "blogger": results[f].get("blogger", ""),
                          "checks": (ph.get("val") or {}).get("checks"),
                          "metrics": (ph.get("val") or {}).get("metrics"),
                          "patterns": [], "n_candidates": 0,
                          "llm_seconds": ph.get("seconds"),
                          "ds_attempts": ph.get("attempts", 0), "glm_attempts": 0,
                          "error": ph.get("error"),
                          "hard_failures": (ph.get("val") or {}).get("hard_failures")}
        write_json({"date": args.date, "target_period": target_period,
                    "target_draw": target_draw, "images": results}, out_path)
        print(f"[pass2] 全 ds：{len(to_phase2)} 张 ds 校验不过 → unresolved", flush=True)
    elif to_phase2 and args.glm_fallback:
        t1 = time.time()
        done = 0
        with ThreadPoolExecutor(max_workers=args.workers_glm) as ex:
            futs = {ex.submit(phase2_glm, f, manifest["images"][f], out_root, lottery,
                              target_period, target_draw, args, results[f]): f
                    for f in to_phase2}
            for fu in as_completed(futs):
                f = futs[fu]
                try:
                    rec = fu.result()
                except Exception as e:
                    import traceback; traceback.print_exc()
                    rec = {"file": f, "decision": "error", "error": f"异常: {e}"}
                rec.setdefault("blogger", blogger_of_file(f, posts_by_id))
                results[f] = rec
                done += 1
                if done % args.save_every == 0:
                    write_json({"date": args.date, "target_period": target_period,
                                "target_draw": target_draw, "images": results}, out_path)
                print(f"[pass2] {done}/{len(to_phase2)} {f[:44]} -> {rec['decision']}", flush=True)
        write_json({"date": args.date, "target_period": target_period,
                    "target_draw": target_draw, "images": results}, out_path)
        print(f"[pass2] DONE {len(to_phase2)} 张 glm 兜底 {time.time()-t1:.0f}s", flush=True)

    # 叙事（可选，并行）
    if not args.no_narrative:
        narr_todo = [f for f in results if results[f].get("decision") in ("ds-ok", "glm-rescue")
                     and results[f].get("patterns") and not results[f].get("analysis_note")]
        if narr_todo:
            with ThreadPoolExecutor(max_workers=args.workers_ds) as ex:
                futs = {ex.submit(add_narrative, results[f], target_period, target_draw): f
                        for f in narr_todo}
                for fu in as_completed(futs):
                    f = futs[fu]
                    results[f] = fu.result()
            write_json({"date": args.date, "target_period": target_period,
                        "target_draw": target_draw, "images": results}, out_path)

    # 汇总
    write_json({"date": args.date, "target_period": target_period,
                "target_draw": target_draw, "images": results}, out_path)
    flat_path = os.path.join(REPO, "data", "crawl", args.date,
                             "image_patterns_with_blogger.json")
    if args.no_flat:
        flat = []
    else:
        flat = build_flat(results, args.keep_digit_run)
        write_json(flat, flat_path)
    report = build_report(results, cropped, target_period, target_draw, args)
    report_path = os.path.join(analysis_dir, "validate_report.json")
    write_json(report, report_path)
    docs = write_docs(report, results, out_root, target_period, target_draw)
    print(f"[analyze] DONE {len(cropped)} 张 → {out_path}")
    print(f"[analyze] 决策: {report['decision_dist']}")
    print(f"[analyze] -> {flat_path}（{len(flat)} 条规律）")
    print(f"[analyze] -> {report_path} / {docs}")


if __name__ == "__main__":
    main()
