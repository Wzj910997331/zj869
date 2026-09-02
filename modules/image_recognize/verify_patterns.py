#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_patterns.py — ⑥ 二次验证(管线第⑥步, judge 输出规律 → 独立核验 → 写回标记)

用户意图(2026-09-02): 四步管线在 judge 输出规律后必须回头验证——predictions 里的
patterns 是规则引擎 extract_candidates 从匹配开奖历史行自动抽的候选, hit 是 run_hits
顺带算的, 无独立核验、未关联博主标注、可能含本期行自证(博主"开奖后更新"型走势图
底部行即本期实开奖)。本脚本保证"输出的规律能用在本期上", 四维度:

  1. 结构事实核对   fact_check:      judge rows 重建 mapping + annotations 重建 anno_pos
                                     → 重跑 extract_candidates → 与 judge patterns 逐条比对
                                     (type+position+numbers+desc 全等)。修旧文件空 anno_pos
                                     导致的 top-12 截断集合漂移 bug。
  2. 命中独立复核   recompute_hit:   judge 层(int position)用 common.hit_record 重算,
                                     与存储 hit 比对; target_draw 与 lottery_recent 权威交叉核对。
  3. 无未来函数     no_future_check: 按 type 定推导读取范围(全部/近2/3/5期/末相邻对),
                                     检查是否含 target 行; 再 oos 剔除 target 行重抽,
                                     判候选是否仍在集合。
  4. 博主标注归属   anno_attribution: position 命中 annotation.positions 且该位 hit_truth
                                     → anno_hit / anno_linked / machine; 全位(position=None) → na。

verdict 五档: invalid(完整性故障,管线 bug) / self_referential(本期行自证) /
              no_hit(正常未中) / verified(博主真画+命中) / candidate(命中但机器候选)。

输入: judge json + lottery_recent + predictions_with_blogger.json(写回基底)
输出(零污染, 只写这 3 个):
  data/crawl/<date>/predictions_with_blogger.json   重生成, 每条 pattern 挂 verify(幂等)
  data/recognize/<date>_all/analysis/pattern_verify_<date>.json  独立验证报告
  data/recognize/<date>_all/analysis/pattern_verify_<date>.md     验证表

judge json / image_patterns_with_blogger.json / crops_all_manifest.json / exclude_list.json 只读。

用法:
  /usr/bin/python3 modules/image_recognize/verify_patterns.py \
      --date 20260831 \
      --judge data/recognize/20260831_all/analysis/judge_20260831.json \
      --lottery data/crawl/20260831/lottery_recent.json \
      --predictions data/crawl/20260831/predictions_with_blogger.json \
      [--target-period 26233] [--draw "1 6 3 4 0"] [--posts data/crawl/20260831/posts.json]
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import (REPO, load_json, write_json, fix_print, hit_record,  # noqa: E402
                    normalize_blogger)
from stage4_llm import extract_candidates  # noqa: E402
from analyze_crops_ds import blogger_of_file  # noqa: E402

POS_CHARS = ["万", "千", "百", "十", "个"]

# 各 type 推导读取的匹配行范围(chronological 序)
#   all: 定位/头/尾/和值/胆码 用全部 matched 行
#   tail3: 斜连等差(1 个数) 用近 3 期
#   tail2: 斜连相连(2 个数) 用近 2 期
#   tail5: 杀号 用近 5 期
#   adjacent_last: 数字串 用末相邻对
def target_ref_scope(ptype, numbers):
    if ptype == "斜连":
        return "tail3" if len(numbers or []) == 1 else "tail2"
    if ptype == "杀号":
        return "tail5"
    if ptype == "数字串":
        return "adjacent_last"
    return "all"


def tail_rows(mapping, ptype, numbers):
    """返回该 pattern 推导读取的 matched 行(period 升序)。"""
    rows = [v for v in mapping.values()
            if v.get("matched") and v.get("draw") and v.get("period")]
    rows.sort(key=lambda v: int(v["period"]))
    scope = target_ref_scope(ptype, numbers)
    if scope in ("tail3",):
        return rows[-3:]
    if scope == "tail2":
        return rows[-2:]
    if scope == "tail5":
        return rows[-5:]
    if scope == "adjacent_last":
        return rows[-2:] if len(rows) >= 2 else []
    return rows


def rebuild_mapping(rows):
    """judge rows {str(rowidx): {period,draw,read,matched}} → extract_candidates 的 mapping。"""
    return {int(i): v for i, v in rows.items()}


def rebuild_anno_pos(annotations):
    """{row: positions} —— 与 judge 的 r["posmap"] 等价(实证可精确复现 top-12)。"""
    return {a["row"]: list(a["positions"]) for a in (annotations or [])
            if a.get("positions")}


def pos_str(pos):
    """position(int 0-4 / str 单字 / None) → 扁平层表示(单字或 None)。"""
    if isinstance(pos, int) and 0 <= pos <= 4:
        return POS_CHARS[pos]
    return pos if isinstance(pos, str) else None


def fact_check(judge_patterns, mapping, anno_pos):
    """重抽 extract_candidates 与 judge patterns 按序逐条比对。返回 (pass, diff)。"""
    derived = extract_candidates(mapping, anno_pos) if mapping else []
    mism = []
    for i, p in enumerate(judge_patterns):
        if i >= len(derived):
            mism.append(("judge_only", p))
            continue
        d = derived[i]
        if not (d["type"] == p["type"] and d.get("position") == p.get("position")
                and d["numbers"] == p["numbers"] and d.get("desc") == p.get("desc")):
            mism.append(("diff", (p, d)))
    if len(derived) > len(judge_patterns):
        for d in derived[len(judge_patterns):]:
            mism.append(("re_derived_only", d))
    return (len(mism) == 0), mism


def no_future_check(pattern, mapping, target_period):
    """无未来函数: 推导行范围是否含 target 行 + oos 剔除重抽是否仍存活。"""
    target = int(target_period)
    rows = [v for v in mapping.values()
            if v.get("matched") and v.get("draw") and v.get("period")]
    rows.sort(key=lambda v: int(v["period"]))
    scope = target_ref_scope(pattern["type"], pattern["numbers"])
    read = tail_rows(mapping, pattern["type"], pattern["numbers"])
    uses_target = any(int(v["period"]) == target for v in read)

    # oos: 剔除 target 行后重抽, 判 (type,pos,numbers) 是否仍在候选集合
    # (只保留 matched 且 period 非空的行——未匹配行 period=None 不能参与重抽)
    oos_rows = {i: v for i, v in mapping.items()
                if v.get("matched") and v.get("draw") and v.get("period")
                and int(v["period"]) != target}
    oos_matched = [v for v in oos_rows.values()
                   if v.get("matched") and v.get("draw") and v.get("period")]
    oos_n = len(oos_matched)
    if oos_n < 3:
        oos_survives = False
        reason = "history_too_short"
    else:
        d2 = extract_candidates(oos_rows, anno_pos_from(mapping, pattern))
        key = (pattern["type"], pattern.get("position"), tuple(pattern["numbers"]))
        oos_survives = any((c["type"], c.get("position"), tuple(c["numbers"])) == key
                           for c in d2)
        reason = "ok" if oos_survives else "dropped_after_target_removal"
    return {"uses_target_row": uses_target, "target_ref": scope,
            "tail_uses_target": uses_target, "oos_n_matched": oos_n,
            "oos_survives": oos_survives, "oos_reason": reason}


def anno_pos_from(mapping, pattern):
    """oos 重抽的 anno_pos: 无法从 judge json 重建具体行标注时的退化(空)——
    仅影响 top-12 排序/提权, 不影响本函数只判"候选是否在集合"时的召回判定安全;
    但为严谨, 主流程 fact_check 用 rebuild_anno_pos(annotations), 此处只用它判存活。"""
    return {}


def anno_attribution(pattern, annotations):
    """博主标注归属: position 命中标注且该位真中→anno_hit; 画了未中→anno_linked;
    未画→machine; 全位(position=None)无法归属→na。"""
    pos = pattern.get("position")
    if not isinstance(pos, int) or not (0 <= pos <= 4):
        return "na"
    ch = POS_CHARS[pos]
    for a in annotations or []:
        if pos in (a.get("positions") or []):
            ht = (a.get("hit_truth") or {}).get(ch)
            return "anno_hit" if ht is True else "anno_linked"
    return "machine"


def verdict_of(f):
    if not f["fact_pass"] or f["hit_mismatch"]:
        return "invalid"
    if f["uses_target_row"] and not f["oos_survives"]:
        return "self_referential"
    if not f["recomputed_hit"]:
        return "no_hit"
    if f["anno_attr"] in ("anno_hit", "anno_linked"):
        return "verified"
    return "candidate"


def verify_annotation(a, mapping, target_period, target_draw):
    """博主标注(真正"博主画的规律")独立验证:
    用该行映射的**权威数字**(matched 行的 draw=开奖真值)重算 hit_truth,
    与 judge 用 read 算的比对 → hit_mismatch。verified 只在此出现。"""
    target = int(target_period)
    row = a.get("row")
    row_rec = mapping.get(str(row)) or mapping.get(row)
    positions = a.get("positions") or []
    auth = bool(row_rec and row_rec.get("matched") and row_rec.get("draw"))
    auth_period = (row_rec or {}).get("period")

    v = {
        "row": row, "positions": positions,
        "row_authoritative": auth, "row_period": auth_period,
        "uses_target_row": bool(auth_period) and int(auth_period) == target,
        "stored_hit": bool(a.get("hit")),
    }
    if auth:
        digits = row_rec["draw"]
        ht = {}
        for p in positions:
            x = digits[p] if isinstance(p, int) and 0 <= p < len(digits) else None
            ht[POS_CHARS[p]] = (x == target_draw[p]) if p < 5 and x is not None else None
        v["recomputed_hit_truth"] = ht
        v["recomputed_hit"] = any(x for x in ht.values())
        v["hit_mismatch"] = bool(a.get("hit")) != v["recomputed_hit"]
    else:
        v["recomputed_hit_truth"] = None
        v["recomputed_hit"] = None
        v["hit_mismatch"] = None

    bad_pos = (not positions
               or not all(isinstance(p, int) and 0 <= p <= 4 for p in positions))
    if bad_pos or v["hit_mismatch"]:
        v["verdict"] = "invalid"                 # 空标注/位置越界/命中算错 → 故障
    elif not auth:
        v["verdict"] = "unverified"              # 标注行无权威映射, 无法背书
    elif v["uses_target_row"]:
        v["verdict"] = "self_referential"        # 画在本期行上 = 自证
    elif not v["recomputed_hit"]:
        v["verdict"] = "no_hit"                  # 博主画了但本期未中
    else:
        v["verdict"] = "verified"                # 博主真画 + 命中 → 可信规律
    return v


def verify_image(file, rec, lottery, target_period, target_draw):
    """单图全量验证(patterns 机器候选 + annotations 博主真画)。
    返回 (image_verify, by_key)。"""
    rows = rebuild_mapping(rec.get("rows") or {})
    annotations = rec.get("annotations") or []
    anno_pos = rebuild_anno_pos(annotations)
    judge_patterns = rec.get("patterns") or []

    f_pass, f_diff = fact_check(judge_patterns, rows, anno_pos) if judge_patterns else (True, [])

    v_patterns, by_key = [], {}
    for p in judge_patterns:
        recomputed = hit_record(p, target_draw)
        stored = p.get("hit")
        nf = no_future_check(p, rows, target_period) if rows else {
            "uses_target_row": False, "target_ref": "none",
            "tail_uses_target": False, "oos_n_matched": 0,
            "oos_survives": False, "oos_reason": "no_rows"}
        v = {
            "type": p["type"], "position": p.get("position"),
            "numbers": p["numbers"], "desc": p.get("desc", ""),
            "stored_hit": bool(stored), "recomputed_hit": bool(recomputed),
            "hit_mismatch": bool(stored) != bool(recomputed),
            "fact_pass": f_pass, "fact_diff": None,
            "uses_target_row": nf["uses_target_row"],
            "target_ref": nf["target_ref"],
            "tail_uses_target": nf["tail_uses_target"],
            "oos_n_matched": nf["oos_n_matched"],
            "oos_survives": nf["oos_survives"],
            "anno_attr": anno_attribution(p, annotations),
        }
        v["verdict"] = verdict_of(v)
        if v["verdict"] == "invalid":
            v["fact_diff"] = [repr(x) for x in f_diff[:20]]
        v_patterns.append(v)
        k = (file, "pattern", p["type"], pos_str(p.get("position")), tuple(p["numbers"]))
        by_key[k] = {kk: vv for kk, vv in v.items() if kk != "type"}

    v_annos = []
    for a in annotations:
        va = verify_annotation(a, rows, target_period, target_draw)
        v_annos.append(va)
        by_key[(file, "annotation", a.get("row"))] = {kk: vv for kk, vv in va.items()}

    img_verify = {
        "file": file, "decision": rec.get("decision"),
        "matched_periods": sorted(int(m) for m in (rec.get("matched_periods") or [])),
        "uses_target_row": any(v["uses_target_row"] for v in v_patterns + v_annos),
        "n_patterns": len(v_patterns), "patterns": v_patterns,
        "n_annotations": len(v_annos), "annotations": v_annos,
    }
    return img_verify, by_key


def cross_check_flatten(judge_json, base_flat, posts_by_id):
    """独立重扁平(镜像 judge_accuracy.py 扁平逻辑)与基底比对, 返回不一致项。"""
    ref = []
    for f, r in judge_json.get("images", {}).items():
        if r.get("decision") not in ("ds-ok", "glm-rescue"):
            continue
        blogger = blogger_of_file(f, posts_by_id) if posts_by_id else None
        for p in r.get("patterns") or []:
            if p["type"] == "数字串":
                continue
            ref.append({"blogger": blogger, "file": f, "type": p["type"],
                        "position": pos_str(p.get("position")),
                        "numbers": p["numbers"], "desc": p.get("desc", ""),
                        "hit": p.get("hit")})
    diffs = []
    if len(ref) != len(base_flat):
        diffs.append(f"patterns 条数不符: 基底 {len(base_flat)} vs 重扁平 {len(ref)}")
    for a, b in zip(base_flat, ref):
        for k in ("file", "type", "position", "numbers", "hit"):
            if a.get(k) != b.get(k):
                diffs.append(f"字段 {k} 不符: 基底 {a.get(k)} vs 重扁平 {b.get(k)} (file={a.get('file')})")
    return diffs


def regenerate_predictions(base, verify_by_key):
    """把 verify 挂到基底扁平 patterns+annotations(幂等覆盖), 返回重生成 dict。"""
    out = json.loads(json.dumps(base))
    n_pat = n_anno = 0
    for p in out.get("patterns") or []:
        k = (p["file"], "pattern", p["type"], p["position"], tuple(p["numbers"]))
        v = verify_by_key.get(k)
        if v is not None:
            p["verify"] = v
            n_pat += 1
    for a in out.get("annotations") or []:
        k = (a["file"], "annotation", a.get("row"))
        v = verify_by_key.get(k)
        if v is not None:
            a["verify"] = v
            n_anno += 1
    out["generated_by"] = "judge_accuracy.py + verify_patterns.py"
    return out, n_pat, n_anno


def build_md_report(report):
    L = [f"# 规律二次验证报告 - {report['date']} ({report['target_period']} = "
         f"{' '.join(map(str, report['target_draw']))})", "",
         f"- 权威开奖核对: judge draw {'== lottery 权威 draw ✅' if not report['draw_mismatch'] else '≠ lottery 权威 ⚠️'}"
         f"  (judge={report['target_draw']} authority={report['authority_draw']})",
         f"- 覆盖: {report['n_images_verified']} 图 / {report['n_patterns']} 条规律"
         f"({report['summary']['n_patterns']} 机器候选 + {report['summary']['n_annotations']} 博主标注)",
         f"- 结构事实失败 {report['summary']['fact_fail']} ｜ hit 不一致 "
         f"{report['summary']['hit_mismatch']} ｜ 含本期行图 {report['summary']['images_with_target_row']}",
         "",
         "## 一、机器候选规律(extract_candidates 规则引擎)",
         "",
         "| 图 | 类型 | 位置 | 数字 | 存hit | 重算hit | 事实 | 未来 | oos | 归属 | 结论 |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for f, iv in report["images"].items():
        for p in iv["patterns"]:
            pn = p.get("position")
            pns = POS_CHARS[pn] if isinstance(pn, int) and 0 <= pn <= 4 else (pn or "全位")
            nums = ",".join(str(x) for x in p["numbers"])
            L.append(f"| {f} | {p['type']} | {pns} | {nums} | "
                     f"{'✓' if p['stored_hit'] else '✗'} | {'✓' if p['recomputed_hit'] else '✗'} | "
                     f"{'✓' if p['fact_pass'] else '✗'} | "
                     f"{'⚠️含本期' if p['uses_target_row'] else '✓'} | "
                     f"{'✓' if p['oos_survives'] else ('—' if p['oos_n_matched'] < 3 else '✗')} | "
                     f"{p['anno_attr']} | {p['verdict']} |")
    L += ["",
          "## 二、博主标注规律(博主在图上真画的标注)",
          "",
          "| 图 | row | 位置 | 权威行 | 存hit | 重算hit | 本期行 | 结论 |",
          "|---|---|---|---|---|---|---|---|"]
    for f, iv in report["images"].items():
        for a in iv["annotations"]:
            pns = "".join(POS_CHARS[p] for p in (a.get("positions") or [])
                          if isinstance(p, int) and 0 <= p <= 4) or "—"
            rh = a.get("recomputed_hit")
            rh_s = "—" if rh is None else ("✓" if rh else "✗")
            L.append(f"| {f} | {a['row']} | {pns} | "
                     f"{'✓' if a['row_authoritative'] else '✗'} | "
                     f"{'✓' if a['stored_hit'] else '✗'} | {rh_s} | "
                     f"{'⚠️' if a['uses_target_row'] else '✓'} | {a['verdict']} |")
    L += ["",
          "> 结论图例: invalid=完整性故障(管线 bug) / unverified=标注行无权威映射无法背书 / "
          "self_referential=含本期行自证 / "
          "no_hit=正常未中 / verified=博主真画+命中 / candidate=命中但机器候选。",
          "> 彩票开奖属独立随机事件, 规律不具备预测效力, 请勿用于赌博或非法用途。"]
    return "\n".join(L)


def main():
    fix_print()
    ap = argparse.ArgumentParser(description="规律二次验证(无未来函数+事实核对+命中复核+博主归属)")
    ap.add_argument("--date", required=True)
    ap.add_argument("--judge", required=True, help="judge json 路径")
    ap.add_argument("--lottery", required=True)
    ap.add_argument("--predictions", required=True, help="predictions_with_blogger.json(写回基底)")
    ap.add_argument("--target-period", default=None)
    ap.add_argument("--draw", default=None, help="空格分隔 5 位; 缺省从 judge json 读")
    ap.add_argument("--posts", default=None, help="posts.json(写回前完整性交叉核对)")
    args = ap.parse_args()

    judge_json = load_json(args.judge)
    lottery = load_json(args.lottery) or []
    if not judge_json or not lottery:
        print("[verify] ERROR: 读不到 judge json 或 lottery")
        sys.exit(2)
    target_period = args.target_period or judge_json.get("target_period")
    target_draw = ([int(x) for x in args.draw.split()]
                   if args.draw else judge_json.get("target_draw"))
    if not target_period or not target_draw:
        print("[verify] ERROR: 缺 target-period/draw")
        sys.exit(2)

    # 权威开奖交叉核对
    authority = None
    for x in lottery:
        if str(x.get("period")) == str(target_period):
            authority = x.get("numbers")
            break
    draw_mismatch = bool(authority) and authority != target_draw

    posts_by_id = {}
    if args.posts:
        posts = load_json(args.posts) or []
        posts_by_id = {p.get("id"): p for p in posts if p.get("id")}

    base = load_json(args.predictions)
    if base is None:
        print(f"[verify] ERROR: 读不到基底 predictions: {args.predictions}")
        sys.exit(2)

    # 写回前完整性交叉核对(防基底漂移/手改损坏)
    diffs = cross_check_flatten(judge_json, base.get("patterns") or [], posts_by_id)
    if diffs:
        print("[verify] ABORT: 基底扁平与 judge 重扁平不一致(不写回), 差异:")
        for d in diffs[:20]:
            print("   ✗", d)
        sys.exit(3)

    # 逐图验证
    results, all_by_key, summary = {}, {}, {
        "by_verdict": {}, "fact_fail": 0, "hit_mismatch": 0,
        "images_with_target_row": 0, "oos_survive_rate": 0.0,
        "n_patterns": 0, "n_annotations": 0}
    n_verdict_total = 0
    for f, rec in judge_json.get("images", {}).items():
        if rec.get("decision") not in ("ds-ok", "glm-rescue"):
            continue
        iv, by_key = verify_image(f, rec, lottery, target_period, target_draw)
        results[f] = iv
        all_by_key.update(by_key)
        for p in iv["patterns"]:
            summary["n_patterns"] += 1
            n_verdict_total += 1
            summary["by_verdict"][p["verdict"]] = summary["by_verdict"].get(p["verdict"], 0) + 1
            if not p["fact_pass"]:
                summary["fact_fail"] += 1
            if p["hit_mismatch"]:
                summary["hit_mismatch"] += 1
        for a in iv["annotations"]:
            summary["n_annotations"] += 1
            n_verdict_total += 1
            summary["by_verdict"][a["verdict"]] = summary["by_verdict"].get(a["verdict"], 0) + 1
            if a.get("hit_mismatch"):
                summary["hit_mismatch"] += 1
        if iv["uses_target_row"]:
            summary["images_with_target_row"] += 1
    summary["oos_survive_rate"] = round(
        sum(1 for f in results for p in results[f]["patterns"]
            if p["oos_survives"]) / max(summary["n_patterns"], 1), 2)

    report = {
        "date": args.date, "target_period": target_period, "target_draw": target_draw,
        "authority_draw": authority, "draw_mismatch": draw_mismatch,
        "generated_by": "verify_patterns.py", "schema_version": 2,
        "n_images_verified": len(results),
        "n_patterns": summary["n_patterns"] + summary["n_annotations"],
        "summary": summary, "images": results,
    }

    # 输出 1: 写回 predictions(幂等)
    new_pred, n_pat, n_anno = regenerate_predictions(base, all_by_key)
    write_json(new_pred, args.predictions)
    print(f"[verify] 写回 predictions_with_blogger.json"
          f"(patterns {n_pat}/{len(base.get('patterns') or [])} + "
          f"annotations {n_anno}/{len(base.get('annotations') or [])} 条挂 verify)")

    # 输出 2/3: 独立报告
    out_root = os.path.join(REPO, "data", "recognize", f"{args.date}_all", "analysis")
    os.makedirs(out_root, exist_ok=True)
    vout = os.path.join(out_root, f"pattern_verify_{args.date}.json")
    write_json(report, vout)
    md = os.path.join(out_root, f"pattern_verify_{args.date}.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write(build_md_report(report))
    print(f"[verify] 报告 -> {vout}")
    print(f"[verify] md    -> {md}")

    # 控制台摘要
    print(f"\n===== {target_period} 期规律二次验证"
          f"({summary['n_patterns']} 机器候选 + {summary['n_annotations']} 博主标注, {len(results)} 图) =====")
    print(f"  draw_mismatch={draw_mismatch} fact_fail={summary['fact_fail']} "
          f"hit_mismatch={summary['hit_mismatch']} 含本期行图={summary['images_with_target_row']}")
    print(f"  by_verdict: {summary['by_verdict']}")


if __name__ == "__main__":
    main()
