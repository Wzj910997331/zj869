#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""reproduce_guihua.py — 现行全流程【最后一步：画规自证复现】。

背景（2026-09-02 二次修正，用户拍板）：
  旧"命中"全靠单一视觉模型（DS/GLM）既读图又判定，无法自证；
  我曾用"像素对校准列"做二次校验，但 filter_report.cols 被期号/和值列污染，
  导致对富老师_0(万1→错改千1)与富老师_2(百7→错改千7)两次把真命改判成 miss。
  用户要求：交付的 json 必须能**只凭 json + 权威开奖表**把博主画规推回来。
  进一步（本轮），用户戳中要害——旧工具只验【值保真】，任何一串真实开奖数字都满足
  值保真，所以我把红线穿过的 5、9 也当成圈点（ⓐ链条超伸），照样"复现成功"。
  → 现在升级成【值保真 + 逻辑自洽】双层：值保真只是"挑的都是真开奖"；
    逻辑自洽要求"预测必须是规律库(节奏/动机/交换/斜连)里某个模式的确定性输出"。
  规律库固定枚举、逐个机械复算；复算能推出预测 → coherent；
  推不出 → incoherent（无规律可推/巧合命中，即使命中也不列为规律）→ 需重读/弃置。
  ① 值保真失败 → ds-fail（DS 读错）→ GLM-flash 兜底重读。
  ② 值保真过、但预测非任何模式输出 → 判"巧合命中"，不列为规律。
  ③ 值保真过、有模式可推 → 记所匹配模式；若链条含"非该模式必需的装饰节点"→ 打超伸警告。

用法：
  python3 tools/reproduce_guihua.py \
      --json data/crawl/20260829/guihua_26231_reproducible.json \
      --lottery data/crawl/20260829/lottery_recent.json
"""
import argparse
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
POS_IDX = {"万": 0, "千": 1, "百": 2, "十": 3, "个": 4}
POS_NAMES = ["万", "千", "百", "十", "个"]


def norm_pos(s):
    return str(s).replace("位", "").strip()


# ---------------------------------------------------------------- 逻辑一致性规律库
# 每个模式函数输入 (series, chains, period)，输出 dict[(pos,digit)] = set(pattern 名)。
# series[pos]  = [(period, value), ...] 按期号升序；chain[pos]=[(period,value)...] 按时序。
RHYTHM = "rhythm"   # 节奏：同列 (a,b) 先前出现且其后跟 c，本次 (a,b) 再现 → 预测 c —— 已废弃（易幻觉，扫表）
MOTIF = "motif"     # 动机：同一数值签名序列在另一列复现且其后接 x → 补齐 x —— 并入 REPEAT
SWAP = "swap"       # 交换：万/十相邻期互换 万[k]=十[k-1]、十[k]=万[k-1] → 互换外推
SLANT = "slant"     # 斜连：同列等差下推 → 下一项
REPEAT = "repeat"   # 签名重复：博主画出的签名串(某位末k个)在博主画的节点里重现、且上次后接X → 预测X


def _vals(series, pos):
    s = series[pos]
    return [v for _, v in s]


def _periods(series, pos):
    return [p for p, _ in series[pos]]


def _find_prior(svals, spers, pattern):
    """在 spers 升序中找 indices[i:i+len(pattern)]==pattern 且满足 period 连续语义。
    返回所有命中段的结束 index；供上层取其后继。仅用于连续期号的等差判据。"""
    n = len(pattern)
    hits = []
    for i in range(len(svals) - n):
        if svals[i:i + n] == pattern:
            hits.append(i + n - 1)  # 命中段最后一个元素的下标
    return hits


def candidates(draw_tbl, chains, period):
    """【严格只从博主画的那张图的线条推导】规律。绝不扫描整张开奖表去找"先例"。

    这是防幻觉的关键（2026-09-02 用户拍板）：任何"更早的别的期 / 别的列里出现过
    (a,b)→c"都是**引入博主没画的上下文**——博主没画的=不存在，一律不得作为规律依据。
    此前我把 26223-26225 万位的"2,9→1"安到用规说话头上，就是扫表找先例产生的幻觉；
    但注意——**博主亲手画出的**签名串重复（如生活很无奈把 2,9,1 在万位圈了两次、
    富老师把 1,4,2,1 在千位与万位各圈一次）**是博主画的轨迹**，属合法规律，按 repeat 计。

    允许的模式（全部只用博主画的节点本身，或用真实开奖值做规则的下推）：
      SWAP    交换 ：博主画出万↔十相邻期互换 万[k]=十[k-1]、十[k]=万[k-1]
                      → 外推 万[T]=十[T-1](真实开奖)、十[T]=万[T-1](真实开奖)
      SLANT   等差 ：博主某位连续画的节点值等距（arithmetic）→ 下一项=末值+步长
      REPEAT  签名重复 ：博主画出的签名串(某位末 k 个值)在博主画的节点里**再次出现**，
                    且上次出现**后面跟着某值 X** → 预测 X（同列之前或跨列均可，只要博主画了）
    其余一律判"无规律可推（博主疑乱画/巧合）"。
    链条约定：chains 只存博主画的**历史已开奖**节点（不含目标期预测节点），预测在 predictions。
    """
    T = str(period)
    out = {}
    add = lambda p, d, pat: out.setdefault((p, d), set()).add(pat)
    chains = chains or {}
    # 归一化：pos -> [(period,value)]，period 转 str、按 period 升序
    nm = {}
    for pos_raw, ch in chains.items():
        pos = norm_pos(pos_raw)
        if not isinstance(ch, list):
            continue
        items = [((str(c["period"])), int(c["digit"])) for c in ch]
        items.sort(key=lambda x: int(x[0]))
        nm[pos] = items

    # ---------- SWAP：万↔十 相邻期互换（只用博主画的万/十节点作证据） ----------
    wp, sp = "万", "十"
    if wp in nm and sp in nm:
        wch, sch = nm[wp], nm[sp]
        wmap = dict(wch)   # period->value（博主画了万位的期）
        smap = dict(sch)   # 博主画了十位的期
        swap_ok = False
        kset = sorted({int(p) for p in wmap} | {int(p) for p in smap})
        for k in kset:
            if str(k) in wmap and str(k - 1) in smap and str(k) in smap and str(k - 1) in wmap:
                if wmap[str(k)] == smap[str(k - 1)] and smap[str(k)] == wmap[str(k - 1)]:
                    swap_ok = True
                    break
        if swap_ok:
            T1 = str(int(T) - 1)
            wprev = draw_tbl[T1][POS_IDX["万"]] if T1 in draw_tbl else None
            sprev = draw_tbl[T1][POS_IDX["十"]] if T1 in draw_tbl else None
            if wprev is not None:
                add(sp, wprev, SWAP)   # 十[T]=万[T-1]
            if sprev is not None:
                add(wp, sprev, SWAP)   # 万[T]=十[T-1]

    # ---------- SLANT：某位博主连续画的节点值等差 → 下一项（只用博主画的节点） ----------
    for pos, items in nm.items():
        vals = [v for _, v in items]
        if len(vals) >= 3:
            diffs = [vals[i + 1] - vals[i] for i in range(len(vals) - 1)]
            if len(set(diffs)) == 1:
                nxt = vals[-1] + diffs[0]
                if 0 <= nxt <= 9:
                    add(pos, nxt, SLANT)
            elif len(vals) >= 3 and len(vals) <= 4:
                # 弱一步：末两值等差的三角斜连（2,5,8 → 1）
                d = vals[-1] - vals[-2]
                if len(diffs) >= 2 and diffs[-1] == diffs[-2]:
                    nxt = vals[-1] + d
                    if 0 <= nxt <= 9:
                        add(pos, nxt, SLANT)

    # ---------- REPEAT：签名串重复（博主画的节点里，某签名串重现且有已知后继） ----------
    # 用法：对某位，取其末 k 个值（当前签名串，即预测前的走势），若博主画的**任一列**(本列更早或
    # 他列)里该签名串**再次出现且后面跟着某值 X**（博主画的），则预测该位=X。取最长命中的签名串，
    # 避免"单一位也重复"的弱匹配。全部用博主画的节点，绝不扫开奖历史表。
    cols = {p: [v for _, v in items] for p, items in nm.items()}
    for pos, vals in cols.items():
        n = len(vals)
        if n < 2:
            continue
        for k in range(n, 1, -1):          # 签名长度：从长到短，取最长有效者
            sig = vals[-k:]
            matched = False
            for cp, cv in cols.items():
                for idx in range(len(cv) - k):
                    if cv[idx:idx + k] == sig:      # 签名串在博主画的节点里重现
                        succ = cv[idx + k]           # 上次出现后跟着的值（博主画的）
                        add(pos, succ, REPEAT)
                        matched = True
            if matched:
                break                         # 最长命中的签名串已定，不再回退到更短签名
    return out


def explain_chain(series, chains, pos, chain):
    """返回该链条被某模式真正使用的节点数 + 模式，用于超伸(装饰节点)诊断。"""
    used_by = {}
    return used_by


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", required=True, help="guihua_<period>_reproducible.json")
    ap.add_argument("--lottery", required=True, help="lottery_recent.json")
    ap.add_argument("--gate-coherence", action="store_true",
                    help="启用逻辑一致性判据（默认启用）")
    ap.set_defaults(gate_coherence=True)
    args = ap.parse_args()
    gui = json.load(open(args.json, encoding="utf-8"))
    lottery = json.load(open(args.lottery, encoding="utf-8"))
    draw_tbl = {str(x["period"]): x["numbers"] for x in lottery}
    drawn = dict(draw_tbl)

    # 每列按期号升序的 (period,value)
    series = {}
    for pname in POS_NAMES:
        idx = POS_IDX[pname]
        series[pname] = []
        for per in sorted(drawn, key=lambda s: int(s)):
            series[pname].append((per, drawn[per][idx]))

    period = str(gui.get("period"))
    images = gui.get("images", {})

    results = {"period": period, "images": {}}
    report_lines = []
    for file, iv in sorted(images.items()):
        blogger = iv.get("blogger", "")
        issues = []
        reproducible = True
        chains = iv.get("chains") or {}

        # ---- ① 值保真：链项必须等于开奖表
        for pos_raw, chain in (iv.get("chains") or {}).items():
            pos = norm_pos(pos_raw)
            idx = POS_IDX.get(pos)
            if idx is None:
                reproducible = False
                issues.append(f"chain位置名无效:{pos}")
                continue
            for c in chain:
                per, dig = str(c.get("period")), c.get("digit")
                drow = draw_tbl.get(per)
                if not drow:
                    reproducible = False
                    issues.append(f"{pos}链 {per} 不在开奖表")
                    continue
                if drow[idx] != dig:
                    reproducible = False
                    issues.append(f"{pos}链 {per}开奖{drow[idx]}≠博主数字{dig}")

        # ---- ② 预测对开奖表 + ③ 逻辑自洽
        cands = candidates(draw_tbl, chains, period) if args.gate_coherence and reproducible else {}
        def _cands():
            return cands

        hit_recs = []
        for p in (iv.get("predictions") or []):
            pos = norm_pos(p.get("position"))
            idx = POS_IDX.get(pos)
            dig = p.get("digit")
            if idx is None:
                reproducible = False
                issues.append(f"预测位置名无效:{pos}")
                continue
            drow = draw_tbl.get(period)
            actual = drow[idx] if drow else None
            hit = (actual == dig)
            if actual is None:
                reproducible = False
                issues.append(f"预测期{period}不在开奖表")

            # 逻辑自洽：预测是否由规律库某模式推出
            pats = cands.get((pos, dig), set())
            coherent = bool(pats)
            hit_recs.append({
                "position": pos, "digit": dig, "actual": actual, "hit": bool(hit),
                "patterns": sorted(pats), "coherent": coherent})
            if args.gate_coherence and not coherent:
                issues.append(f"预测{pos}{dig}: 规律库无法推出(无逻辑自洽，疑似巧合)")

        # ---- 超伸诊断：链条节点 vs 被模式真正使用的节点
        overshoot = []
        if args.gate_coherence and reproducible:
            for pos_raw, chain in (chains or {}).items():
                pos = norm_pos(pos_raw)
                vals = [c["digit"] for c in chain] if isinstance(chain, list) else []
                # 记录该链条被哪些 pattern 用到的最少节点数（启发式：rhythm 用末2、motif/repeat 用全链、slant 用末3）
                used = {"rhythm": min(len(vals), 2), "motif": len(vals), "repeat": len(vals),
                        "slant": min(len(vals), 3)} if vals else {}
                # 粗略：若链条有 ≥3 节点且无任何模式把整条链用上，可疑
                if len(vals) >= 3:
                    # 该位置是否有预测命中并 coherent（即模式存在）？模式若只用末2，则前段悬空
                    hv = [h for h in hit_recs if h["position"] == pos]
                    if hv and all(h["coherent"] for h in hv):
                        # 找出解释该预测的模式
                        pats = set()
                        for h in hv:
                            pats |= set(h["patterns"])
                        # repeat/motif 整链都用(先一次出现+当前一次)；swap 精确；rhythm/slant 用尾段
                        if pats & {REPEAT} or pats & {MOTIF}:
                            neednodes = len(vals)
                        elif pats & {SWAP}:
                            neednodes = len(vals)
                        else:
                            neednodes = 2 if (pats & {RHYTHM}) else min(len(vals), 3)
                        if len(vals) - neednodes >= 2:
                            overshoot.append(f"{pos}链{len(vals)}节点但模式({','.join(sorted(pats))})仅用{neednodes}个→前段{len(vals)-neednodes}个为装饰/超伸(疑读入红线穿过的点)")
            if overshoot:
                issues += overshoot

        # ---- 判定
        if not reproducible:
            verdict = "ds-fail"
        else:
            any_hit = any(h["hit"] for h in hit_recs)
            if args.gate_coherence:
                all_coherent = all(h["coherent"] for h in hit_recs if h["actual"] is not None) \
                               or any(h["coherent"] for h in hit_recs)
                if not all(h["coherent"] for h in hit_recs if h["hit"]):
                    # 存在命中但无逻辑自洽 → 巧合命中，不算规律（即使命中）
                    verdict = "coincidence"
                elif any_hit:
                    verdict = "ok"
                else:
                    verdict = "reproducible-no-hit"
            else:
                verdict = "ok" if any_hit else "reproducible-no-hit"

        results["images"][file] = {
            "blogger": blogger, "reproducible": reproducible, "verdict": verdict,
            "issues": issues, "predictions": hit_recs,
            "need_glm_fallback": not reproducible}
        hit_str = ";".join(
            f"{h['position']}{h['digit']}/实{h['actual']}{'✓' if h['hit'] else '✗'}"
            f"{('['+','.join(h['patterns'])+']') if h.get('coherent') else '[无逻辑]'}"
            for h in hit_recs)
        report_lines.append(
            f"  {'✓' if verdict == 'ok' else ('Δ' if verdict == 'reproducible-no-hit' else ('✗ ds-fail' if verdict == 'ds-fail' else '! 巧合(无逻辑)'))} "
            f"{blogger:12s} {verdict}"
            + (f"  ← {issues}" if issues else "") + (f"  [{hit_str}]"))

    n_ok = sum(1 for v in results["images"].values() if v["verdict"] == "ok")
    n_coinc = sum(1 for v in results["images"].values() if v["verdict"] == "coincidence")
    n_dsfail = sum(1 for v in results["images"].values() if v["verdict"] == "ds-fail")
    n_no = sum(1 for v in results["images"].values() if v["verdict"] == "reproducible-no-hit")
    print(f"画规自证复现（值保真+逻辑自洽） — {period}")
    print("\n".join(report_lines))
    print(f"  可复现且中(逻辑自洽) {n_ok} ｜ 巧合命中(无逻辑,不列规律) {n_coinc} ｜ "
          f"可复现但未中 {n_no} ｜ 复现失败(ds-fail,需GLM兜底) {n_dsfail}")

    with open(os.path.splitext(args.json)[0] + ".verdict.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    return results


if __name__ == "__main__":
    r = main()
    sys.exit(0)
