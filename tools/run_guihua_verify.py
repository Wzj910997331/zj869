#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_guihua_verify.py — 命中图画规判断流程（⑥+curate+⑦+稳健性），一键可复用。

2026-09-02 由 26232 期 ①–⑦ 手动跑法沉淀。它把我当时手工做的四件事收成一条命令：
  1. ⑥ 读整张原图画规：对每张命中图做 **DS vision 有界单次** struct×2+narr×1
     （绕过 read_guihua.py 的 GLM 240s×4 死循环；DS 网关抖动即超时弃，不重试，收 2/3 即可）
  2. curate：从成功的 struct 盲读里抽「画笔元素」，**值保真过滤**（节点数字必须==该期该位真实开奖，
     剔行错位/幻觉节点）组 chains；预测一律取 ⑤ export docs json 里博主已声明的手写单码
     （predicted_positions），**绝不**用视觉读的行锚定（模型常把目标期手写错锚到校准行）
  3. ⑦ 值保真+逻辑自洽自证复现（复用 tools/reproduce_guihua.py）
  4. 稳健性（防 26231"链条读不全→误判巧合"）：coincidence 时做**上界变体敏感性扫描**
     （博主把目标前 10 期整列全画）→ 上界仍推不出命中预测 ⇒ coincidence 与链完整度无关

用法：
  python3 tools/run_guihua_verify.py \
      --period 26232 --draw "8 0 2 3 3" --calib 26231 --calib-draw "1 8 7 9 9" \
      --hits docs/规律/26232.json \
      --images data/crawl/20260830/images \
      --lottery data/crawl/20260830/lottery_recent.json \
      --outdir data/crawl/20260830 \
      [--skip-reads]          # 已有 reads json，不再调视觉
      [--reads <reads.json>]  # 复用旧 reads（默认 <outdir>/guihua_<period>_reads.json）
      [--timeout 120]         # 单次视觉调用上限（>此即弃，防推理死循环）
      [--no-robust]           # 跳过上界变体稳健性扫描

产物：
  <outdir>/guihua_<period>_reads.json              ⑥ 原始读数（不入库）
  <outdir>/guihua_<period>_reproducible.json       ⑦ 输入（chains+预测，不入库）
  <outdir>/guihua_<period>_reproducible.verdict.json  ⑦ 判决（不入库）
  终端汇总表：每命中图 verdict(ok/coincidence/ds-fail) + 若 coincidence 是否 robust
"""
import argparse
import base64
import io
import json
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "modules", "image_recognize"))

from PIL import Image  # noqa: E402

import analyze_crops_ds as ac  # noqa: E402  (call_ds_vision, DS_MODEL)
import read_guihua as rg  # noqa: E402  (PROMPT_STRUCT, PROMPT_NARR, extract_json_obj)

POS_IDX = {"万": 0, "千": 1, "百": 2, "十": 3, "个": 4}


def norm_pos(s):
    return str(s).replace("位", "").strip()


# ---------------------------------------------------------------- ① 加载命中清单
def load_hits(docs_json):
    """从 ⑤ export 的 docs/规律/<period>.json rules[] 提取命中图清单。

    每个 rule: {blogger, image, predicted_positions:[{位置,候选[单码],...}], ...}
    返回 [{file, blogger, image_path, predictions:[{position,digit}], desc}]
    predictions 只取 单码(候选长1) 且 位在 万千百十个 —— 口径=博主目标期行手写单押。
    """
    d = json.load(open(docs_json, encoding="utf-8"))
    base = os.path.dirname(os.path.abspath(docs_json))
    hits = []
    for r in d.get("rules") or []:
        preds = []
        for p in r.get("predicted_positions") or []:
            pos = norm_pos(p.get("位置", ""))
            cand = p.get("候选") or []
            if pos in POS_IDX and len(cand) == 1 and isinstance(cand[0], int):
                preds.append({"position": pos + "位", "digit": cand[0]})
        img = r.get("image") or r.get("file")
        if not img:
            continue
        hits.append({
            "file": img,
            "blogger": r.get("blogger", ""),
            "predictions": preds,
            "desc": r.get("画规类型", ""),
        })
    return d, hits


# ---------------------------------------------------------------- ② ⑥ DS 有界读图
def image_b64(path, max_side=1280):
    im = Image.open(path).convert("RGB")
    w, h = im.size
    m = max(w, h)
    sc = min(1.0, max_side / m)
    im = im.resize((int(w * sc), int(h * sc)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode(), im.size


def ds_read(prompt, b64, timeout):
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]}]
    t0 = time.time()
    raw = ac.call_ds_vision(msgs, timeout=timeout, max_tokens=16000)
    return raw, round(time.time() - t0)


def do_reads(image_path, period, draw, calib, calib_draw, timeout, n_struct=2):
    """单张命中图 ⑥：struct 盲读×n + narr×1。返回 reads 列表(含 ok/秒/字段/raw)。"""
    b64, size = image_b64(image_path)
    calib_txt = (f"上一期 {calib} 开奖 = {calib_draw}(万=第1个 千=第2 百=第3 十=第4 个=第5);"
                 "请先找到这行,用它确认「哪个格是万位」,再读博主标注。")
    p_struct = rg.PROMPT_STRUCT.format(calib_txt=calib_txt, period=period)
    p_narr = rg.PROMPT_NARR.format(calib_period=calib, calib_draw=calib_draw,
                                   period=period, draw=draw)
    reads = []
    for i in range(1, n_struct + 1):
        try:
            raw, dt = ds_read(p_struct, b64, timeout)
        except Exception as e:
            reads.append({"variant": "struct", "run": f"struct#{i}", "ok": False,
                          "秒": timeout, "画法描述": f"__ERROR__ {e}"[:300]})
            continue
        obj = rg.extract_json_obj(raw) if raw else None
        it = {"variant": "struct", "run": f"struct#{i}", "model": ac.DS_MODEL,
              "ok": bool(obj), "秒": dt}
        if obj is not None:
            for k in ("画规类型", "画笔元素", "画法描述", "推导逻辑", "预测"):
                it[k] = obj.get(k, "")
            it["raw"] = raw
        else:
            it["画法描述"] = (raw or "")[:300]
        reads.append(it)
    try:
        raw, dt = ds_read(p_narr, b64, timeout)
    except Exception as e:
        reads.append({"variant": "narr", "run": "narr#1", "ok": False, "秒": timeout,
                      "画法描述": f"__ERROR__ {e}"[:300]})
    else:
        reads.append({"variant": "narr", "run": "narr#1", "model": ac.DS_MODEL,
                      "ok": bool(raw), "秒": dt, "画法描述": (raw or "")[:2000], "raw": raw})
    return reads


def ensure_reads(period, draw, calib, calib_draw, hits, images_dir, outdir,
                 reads_path, skip_reads, timeout):
    out = {"period": period, "draw": draw, "calib": calib, "calib_draw": calib_draw,
           "说明": f"⑥ DS vision 有界单次(struct+narr); {timeout}s 超时即弃不重试; 2026-09-02 工具沉淀",
           "images": {}}
    if skip_reads:
        if reads_path and os.path.exists(reads_path):
            out = json.load(open(reads_path, encoding="utf-8"))
            return out, False
        out["说明"] += " [--skip-reads 但无既有 reads → 空]"
        return out, True
    # 复用既有 ok struct(>=1) 的图，避免重复烧网关
    existing = {}
    if reads_path and os.path.exists(reads_path):
        existing = json.load(open(reads_path, encoding="utf-8")).get("images", {})
    did_net = False
    for h in hits:
        fp = os.path.join(images_dir, h["file"])
        ex = existing.get(h["file"])
        if ex and any(r.get("ok") and r.get("variant") == "struct" for r in ex.get("reads", [])) \
                and any(r.get("ok") and r.get("variant") == "narr" for r in ex.get("reads", [])):
            out["images"][h["file"]] = ex
            print(f"  复用既有读数: {h['blogger']} ({h['file']})")
            continue
        print(f"  ⑥ DS 读: {h['blogger']} ({h['file']}) ...")
        reads = do_reads(fp, period, draw, calib, calib_draw, timeout)
        nok = sum(1 for r in reads if r["ok"])
        print(f"    -> {nok}/{len(reads)} ok " +
              "; ".join(f"{r['run']}:{'OK' if r['ok'] else 'FAIL'}{r['秒']}s" for r in reads))
        out["images"][h["file"]] = {
            "file": h["file"], "blogger": h["blogger"],
            "hits": [{"位置": p["position"], "候选": [p["digit"]]} for p in h["predictions"]],
            "reads": reads}
        did_net = True
    os.makedirs(outdir, exist_ok=True)
    if reads_path:
        json.dump(out, open(reads_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return out, did_net


# ---------------------------------------------------------------- ③ curate reproducible
def curate(reads, hits, lottery):
    """从每个 struct 盲读的画笔元素抽 chains（值保真过滤）; 预测取 hits 声明的单码。"""
    draw_tbl = {str(x["period"]): x["numbers"] for x in lottery}
    images = {}
    for h in hits:
        rset = reads.get("images", {}).get(h["file"], {}).get("reads", [])
        struct = next((r for r in rset if r.get("variant") == "struct" and r.get("ok")), None)
        narr = next((r for r in rset if r.get("variant") == "narr" and r.get("ok")), None)
        chains = {}
        drops = []
        elems = (struct or {}).get("画笔元素") or []
        if isinstance(elems, list):
            seen = {}
            for e in elems:
                if not isinstance(e, dict):
                    continue
                pos = norm_pos(e.get("位置", ""))
                per = str(e.get("期号", ""))
                dg = e.get("数字")
                if pos not in POS_IDX or per not in draw_tbl or not isinstance(dg, int):
                    continue
                if int(per) >= int(reads["period"]):
                    continue  # 目标期预测节点不算链条
                if draw_tbl[per][POS_IDX[pos]] == dg:
                    seen.setdefault(pos, {})[per] = dg
                else:
                    drops.append({"期": per, "位": pos, "数字": dg,
                                  "实际": draw_tbl[per][POS_IDX[pos]]})
            chains = {p: sorted([{"period": int(k), "digit": v} for k, v in d.items()],
                                key=lambda x: x["period"]) for p, d in seen.items()}
        img = {
            "blogger": h["blogger"], "read_source": "ds-visual",
            "画规类型": (struct or {}).get("画规类型", ""),
            "画法描述": (struct or {}).get("画法描述", "") or (narr or {}).get("画法描述", ""),
            "画法描述_narr": (narr or {}).get("画法描述", ""),
            "chains": chains,
            "predictions": [{"position": p["position"], "digit": p["digit"]}
                            for p in h["predictions"]],
            "struct_read_ok": bool(struct),
            "dropped_nodes": drops,
        }
        if not struct:
            img["画法描述"] = (img["画法描述"] or "") + " [⚠️ 无 ok struct 盲读 → ds-fail]"
        images[h["file"]] = img
    return images


def build_reproducible(period, draw, calib, calib_draw, images, outdir):
    out = {"period": period, "draw": draw, "calib": calib, "calib_draw": calib_draw,
           "method": "⑦ 自证复现", "pos_index": POS_IDX,
           "说明": "chains=struct 盲读中值保真通过的历史节点(剔行错位/幻觉, 见各图 dropped_nodes); 预测=博主声明的手写单码",
           "images": images}
    p = os.path.join(outdir, f"guihua_{period}_reproducible.json")
    json.dump(out, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return p


# ---------------------------------------------------------------- ④ ⑦ + 稳健性
def run_reproduce(repro_json, lottery_path):
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reproduce_guihua.py")
    r = subprocess.run([sys.executable, script, "--json", repro_json,
                        "--lottery", lottery_path],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(r.stdout)
    if r.stderr:
        print("STDERR:", r.stderr[-1200:])
    verdict_path = os.path.splitext(repro_json)[0] + ".verdict.json"
    if os.path.exists(verdict_path):
        return json.load(open(verdict_path, encoding="utf-8"))
    return None


def upper_bound_chains(lottery, period, window=10):
    """上界变体：博主把 target-window..target-1 整列全画（链完整度上界）。"""
    rows = sorted([int(x["period"]) for x in lottery])
    lo = int(period) - window
    pers = [str(p) for p in rows if lo <= p < int(period)]
    byp = {str(x["period"]): x["numbers"] for x in lottery}
    ch = {}
    for pos, idx in POS_IDX.items():
        ch[pos + "位"] = [{"period": int(p), "digit": byp[p][idx]} for p in pers]
    return ch


def robust_scan(verdict, lottery, period):
    """对 coincidence/ds-fail 判定做上界稳健性: 上界链能否推出命中预测。"""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
    import reproduce_guihua as rp
    notes = []
    for file, iv in verdict.get("images", {}).items():
        if iv.get("verdict") != "coincidence":
            continue
        if not iv.get("reproducible"):
            notes.append(f"{file}: ds-fail(值保真失败) 非 coincidence")
            continue
        ch = upper_bound_chains(lottery, period)
        cands = rp.candidates({str(x["period"]): x["numbers"] for x in lottery},
                              ch, str(period))
        derivable = [(h["position"], h["digit"]) for h in iv.get("predictions", [])
                     if (norm_pos(h["position"]), h["digit"]) in cands]
        if derivable:
            notes.append(f"{iv.get('blogger')}: ⚠️ 上界变体可推出 {derivable} → "
                         "coincidence 与链完整度相关，需人工复核博主是否画了这些节点")
        else:
            notes.append(f"{iv.get('blogger')}: coincidence 稳健(上界整列全画仍推不出命中预测)")
    return notes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", required=True)
    ap.add_argument("--draw", required=True)
    ap.add_argument("--calib", required=True)
    ap.add_argument("--calib-draw", required=True)
    ap.add_argument("--hits", required=True, help="⑤ 导出的 docs/规律/<period>.json")
    ap.add_argument("--images", required=True, help="原图目录 .../images")
    ap.add_argument("--lottery", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--skip-reads", action="store_true")
    ap.add_argument("--reads", default=None)
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--no-robust", action="store_true")
    args = ap.parse_args()

    print(f"== 命中图画规判断流程 {args.period} ==")
    _, hits = load_hits(args.hits)
    if not hits:
        print("docs json rules[] 为空 → 无命中图可送画规 (规律 0 收尾即可)")
        return
    print(f"命中图 {len(hits)} 张: " + ", ".join(f"{h['blogger']}({len(h['predictions'])}码)"
          + ": " + "".join(f"{p['position'].replace('位','')}{p['digit']} " for p in h['predictions']) for h in hits))

    reads_path = args.reads or os.path.join(args.outdir, f"guihua_{args.period}_reads.json")
    reads, did_net = ensure_reads(args.period, args.draw, args.calib, args.calib_draw,
                                  hits, args.images, args.outdir, reads_path,
                                  args.skip_reads, args.timeout)
    lottery = json.load(open(args.lottery, encoding="utf-8"))
    images = curate(reads, hits, lottery)
    repro_json = build_reproducible(args.period, args.draw, args.calib, args.calib_draw,
                                    images, args.outdir)
    print(f"reproducible json -> {repro_json}")
    verdict = run_reproduce(repro_json, args.lottery)
    if verdict is None:
        print("⑦ 未产出 verdict json"); return
    if not args.no_robust:
        notes = robust_scan(verdict, lottery, args.period)
        if notes:
            print("== 稳健性(上界变体敏感性, 防链条读不全误判巧合) ==")
            for n in notes:
                print("  " + n)


if __name__ == "__main__":
    main()
