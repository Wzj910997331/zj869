#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""eval_hand_cols.py — cols strip CV 逐位读的 26231 离线评测（exploratory）。

输入：26231 manifest(strips) + answer_key(人工 A/B/C) + hand_cnn.pt。
输出：每案例每位置的提取/读结果表 + 汇总：
  - A 类上：能读对几个博主手写单码、需要 DS 兜底（提取失败/uncertain）的比例；
  - B 画规(空圈/连线) 上：是否被误判成"干净单字"→ 评估圆→0 幻觉风险。
用法：python3 modules/image_recognize/eval_hand_cols.py [--tau 0.6] [--gap 0.15]
"""
import argparse, json, os, re, sys
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "model"))
import hand_ocr as HO
from digit_cnn import load_model, uncertain_decision

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATE = "20260829"
STRIPS = os.path.join(REPO, "data", "crawl", DATE, "strips")
MAN = json.load(open(os.path.join(STRIPS, "manifest.json")))["images"]
AK = json.load(open(os.path.join(REPO, "debug", "handwriting_eval", "answer_key.json")))
HAND_PT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "model", "hand_cnn.pt")
COL = HO.COL_POS


def gold_map(c):
    """返回 (pos->[digits] dict, 'any'digits or None, unknown:bool)。"""
    t = (c.get("text") or "").replace(" ", "")
    note = c.get("note") or ""
    m = re.search(r"@([万千百十个]+)$", note.replace("，", ""))
    if m and len(t) == len(m.group(1)):
        return {p: [int(d)] for p, d in zip(m.group(1), t)}, False
    if len(t) == 5 and "/" not in t and all(ch.isdigit() for ch in t):
        return {p: [int(d)] for p, d in zip(COL, t)}, False
    # 单码无位置：任一列颜色都算该字
    if "/" not in t and all(ch.isdigit() for ch in t):
        return {c: [int(d)] for c, d in zip(COL, t)}, True
    return None, True


def strip_path(c):
    return os.path.join(STRIPS, os.path.splitext(c["file"])[0] + "_strip.png")


def load_tile(c):
    return np.asarray(Image.open(strip_path(c)).convert("RGB")).astype(int)


def classify_all(model, tile, res):
    """对 res 里 ok 且 not multi 的候选补 conf。返回每位置 dict 更新。"""
    for p, r in res.items():
        if not r.get("ok") or r.get("multi"):
            r["digit"], r["conf"], r["gap"] = None, 0.0, 0.0
            continue
        g = HO.glyph_stroke_crop(tile, r["x0"], r["box"])
        out = HO.classify_glyph(g, model=model)
        if out is None:
            r["digit"], r["conf"], r["gap"] = None, 0.0, 0.0
        else:
            r["digit"], r["conf"], _, r["gap"] = out
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tau", type=float, default=0.55)
    ap.add_argument("--gap", type=float, default=0.10)
    args = ap.parse_args()
    model = load_model(device="cpu", path=HAND_PT)
    print(f"hand_cnn: {model is not None}  ({HAND_PT})")

    cases = [c for c in AK["images"] if c.get("strip_type") == "cols"]
    rows = []
    stat = {"A_ok_pos": 0, "A_bad_pos": 0, "A_read_hit": 0, "A_read_miss": 0,
            "A_conf_hi": 0, "B_glyph_ok": 0, "B_conf_hi": 0, "C_glyph_ok": 0}
    for c in sorted(cases, key=lambda x: x["idx"]):
        meta = MAN.get(c["file"])
        if not meta:
            print(f"idx{c['idx']:<3}[{c['class']}] MISSING manifest")
            continue
        tile = load_tile(c)
        res = HO.extract_pos_glyphs(tile, meta)
        res = classify_all(model, tile, res)
        gm, unknown = gold_map(c)
        line = [f"idx{c['idx']:<3} {c['class']} {c['blogger'][:8]:<9}"]
        hit_pos, det_pos, total_pos = 0, 0, 0
        for p in COL:
            r = res[p]
            if gm and p in gm:
                total_pos += 1
            tag = []
            if r.get("ok") and not r.get("multi"):
                det_pos += 1
                d, conf, gap = r["digit"], r["conf"], r["gap"]
                confident = conf >= args.tau and gap >= args.gap
                if d is not None and d == 10:
                    tag.append(f"{p}=非字?")
                    continue
                gold = (gm or {}).get(p)
                tag.append(f"{p}:{'' if d is not None else '-'}@{conf:.2f}/{gap:.2f}"
                           f"{'' if confident else '↓uncert'}"
                           f"{' [=gold%d]' % gold[0] if gold and d == gold[0] else (' [G%d!=%s]' % (gold[0], d) if gold else '')}")
            elif r.get("ok") and r.get("multi"):
                tag.append(f"{p}:multi(≥2)")
            else:
                tag.append(f"{p}:{r.get('reason')}")
            line.append(" ".join(tag))
        print("  " + " | ".join(line))

    # aggregate over per-position decisions (A gold positions)
    # recompute inside same loop is messy → second pass
    agg = []
    for c in [x for x in cases if x.get("class") == "A"]:
        meta = MAN.get(c["file"])
        if not meta:
            continue
        tile = load_tile(c)
        res = HO.extract_pos_glyphs(tile, meta)
        res = classify_all(model, tile, res)
        gm, unknown = gold_map(c)
        for p in COL:
            if not gm or p not in gm:
                continue
            r = res[p]
            gold = gm[p][0]
            if not (r.get("ok") and not r.get("multi")):
                agg.append(("fallback", p, gold, None, 0))
                continue
            d, conf, gap = r["digit"], r["conf"], r["gap"]
            confident = conf >= args.tau and gap >= args.gap
            if not confident:
                agg.append(("uncertain", p, gold, d, conf))
            elif d == gold:
                agg.append(("HIT", p, gold, d, conf))
            else:
                agg.append(("MISS", p, gold, d, conf))
    from collections import Counter
    cnt = Counter(a[0] for a in agg)
    print(f"\nA/cols 有 gold 位置 {len(agg)} 个："
          f"提取失败转DS(no-glyph/几何) {cnt['fallback']}、读低置信转DS {cnt['uncertain']}、"
          f"读对 {cnt['HIT']}、读错 {cnt['MISS']}")
    if cnt["HIT"] + cnt["MISS"]:
        print(f"  CV 自吸收（高置信读）准确率 = {cnt['HIT']}/({cnt['HIT']}+{cnt['MISS']}) "
              f"= {cnt['HIT']/(cnt['HIT']+cnt['MISS'])*100:.0f}%")

    # B/C false-positive watch
    from collections import Counter as C2
    bc = []
    for c in [x for x in cases if x.get("class") in ("B", "C")]:
        meta = MAN.get(c["file"])
        if not meta:
            continue
        tile = load_tile(c)
        res = HO.extract_pos_glyphs(tile, meta)
        res = classify_all(model, tile, res)
        for p in COL:
            r = res[p]
            if r.get("ok") and not r.get("multi"):
                d, conf, gap = r["digit"], r["conf"], r["gap"]
                confident = conf >= args.tau and gap >= args.gap
                bc.append((c["class"], "conf" if confident else "weak", d))
    bcm = C2(bc)
    print(f"\nB/C 误读警示（空圈/画规/多码本应转 DS）：B/C cols 窗口被判'单字'且高置信的 "
          f"{sum(v for k, v in bcm.items() if k[1] == 'conf')} 个，分布 {dict((str(k), v) for k, v in bcm.items() if k[1] == 'conf')}")


if __name__ == "__main__":
    main()
