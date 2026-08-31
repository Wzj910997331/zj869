#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
规律验证工具 — 无未来函数回测

对 `docs/规律/<期号>.json` 中每条规律做三层验证：
  1. 结构事实核对：logic 引用的历史(期号,位置,数字) 是否真实存在；
  2. 候选命中 vs 随机：候选集大小决定随机基准（1码=10%、2码=20%/位置），
     本期命中位置数与随机期望比较；
  3. 家族滚动回测：可机械复现的画法家族（X形交叉 / 隔期摆动 / 列内3连等差）
     在历史窗口内滚动外推下一期，统计触发次数与命中率。

无未来函数约束：
  - 任何回测判定"触发"只使用目标期 T 之前（≤T-1）的开奖数据；
  - 命中判定使用 T 期开奖（这是已知真值，用于打分，不参与触发）；
  - 回看窗口内出现博主同款结构才计入触发（规律只在窗口内有效）。

用法：
  python tools/verify_rules.py \
      --rules docs/规律/26230.json \
      --draws data/crawl/20260828/lottery_recent.json \
      --out  data/crawl/20260828/rule_verify_26230.json \
      --inplace          # 把 verify 字段写回每条规律
      --md  docs/规律/26230_验证.md   # 生成验证 md
"""
import argparse
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

POS = {"万": 0, "千": 1, "百": 2, "十": 3, "个": 4}
POS_NAME = {0: "万", 1: "千", 2: "百", 3: "十", 4: "个"}

# ---- 画法家族关键词映射（用于把规律归到可回测家族）----
FAMILY_RULES = {
    "xcross":     {"label": "X形交叉", "kw": lambda s: "X形" in s or ("交叉" in s and "对称" in s)},
    "alternation": {"label": "隔期摆动", "kw": lambda s: any(k in s for k in ["摆动", "交替", "隔期", "循环", "回补"])},
    "ap3":        {"label": "列内3连等差", "kw": lambda s: "等差" in s or ("竖排" in s and "连号" in s)},
}


def extract_refs(logic, target):
    """从 logic 文本提取 (period, position, digit) 历史引用（排除目标期）。"""
    refs = set()
    # 26227万4 / 26228个位5（紧邻，数字后不能再跟数字）
    for m in re.finditer(r"(262\d\d)(万|千|百|十|个)位?(\d)(?!\d)", logic):
        p, po, dig = m.groups()
        if p != target:
            refs.add((p, POS[po], int(dig)))
    # 万26228=5 / 十位26226=6（位置字紧贴期号）
    for m in re.finditer(r"(万|千|百|十|个)位?(262\d\d)[^0-9]{0,3}(\d)(?!\d)", logic):
        po, p, dig = m.groups()
        if p != target:
            refs.add((p, POS[po], int(dig)))
    # 万位9(26224)
    for m in re.finditer(r"(万|千|百|十|个)位?(\d)\((262\d\d)\)", logic):
        po, dig, p = m.groups()
        if p != target:
            refs.add((p, POS[po], int(dig)))
    # 3(26223百)
    for m in re.finditer(r"(\d)\((262\d\d)(万|千|百|十|个)\)", logic):
        dig, p, po = m.groups()
        if p != target:
            refs.add((p, POS[po], int(dig)))
    # 万位(26227~26229: 4、5、2) 及继承区间的 十位(9、2、5)
    last = None
    for seg in re.split(r"[；;。]", logic):
        mr = re.search(r"(万|千|百|十|个)位?\((\d{5})[~-](\d{5}):\s*([\d、]+)\)", seg)
        if mr:
            po, p0, p1, digs = mr.groups()
            vals = [int(x) for x in digs.split("、")]
            ps = list(range(int(p0), int(p1) + 1))
            last = (POS[po], ps)
            for p, v in zip(ps, vals):
                if str(p) != target:
                    refs.add((str(p), POS[po], v))
        else:
            mb = re.search(r"(万|千|百|十|个)位?\(([\d、]+)\)", seg)
            if mb and last:
                po, digs = mb.groups()
                vals = [int(x) for x in digs.split("、")]
                pos, ps = last
                for p, v in zip(ps, vals):
                    if str(p) != target:
                        refs.add((str(p), pos, v))
    return refs


def fact_check(rule, draws, target):
    """核对 logic 引用的历史数字是否真实存在。返回错误列表（空=通过）。"""
    errors = []
    refs = extract_refs(rule.get("logic", ""), target)
    for (p, po, dig) in sorted(refs):
        if p not in draws:
            errors.append(f"{p}期缺失")
        elif draws[p][po] != dig:
            errors.append(f"{p}{POS_NAME[po]}实际{draws[p][po]}≠声称{dig}")
    return errors, refs


def lookback_window(logic, target):
    """回看窗口：logic 引用的最早历史期号 → 目标期 的跨度。"""
    pnums = sorted({int(m) for m in re.findall(r"262\d\d", logic) if int(m) <= int(target)})
    hist = [p for p in pnums if p != int(target)]
    if not hist:
        return None, None
    mref = min(hist)
    return mref, int(target) - mref


def random_expect(rule):
    """候选集随机期望命中位置数 = Σ(候选数/10)。"""
    return round(sum(len(p.get("候选", [])) / 10 for p in rule.get("predicted_positions", [])), 2)


def actual_hits(rule):
    """本期实际命中位置数（用 pos_check 的 ✓ 统计）。"""
    return sum(1 for v in rule.get("pos_check", {}).values() if "✓" in v)


def classify_family(logic):
    """按关键词把规律归到可回测家族。"""
    fam = [key for key, f in FAMILY_RULES.items() if f["kw"](logic)]
    return fam or ["subjective"]


# ============ 家族滚动回测（无未来函数）============

def backtest_xcross(draws, order, gap):
    """X形交叉：万(T-2g)=十(T-g) 且 十(T-2g)=万(T-g) → 万/十 ∈ {A,B}。"""
    res = {"trig": 0, "hit_w": 0, "hit_s": 0, "hit_any": 0}
    for T in order:
        t2, t1 = str(int(T) - 2 * gap), str(int(T) - gap)
        if t2 not in draws or t1 not in draws:
            continue
        A, B = draws[t2][0], draws[t2][3]
        if draws[t1][0] == B and draws[t1][3] == A and A != B:
            res["trig"] += 1
            hw = draws[T][0] in {A, B}
            hs = draws[T][3] in {A, B}
            res["hit_w"] += hw
            res["hit_s"] += hs
            res["hit_any"] += (hw or hs)
    return res


def backtest_alternation(draws, order):
    """隔期摆动：数字 d 在 万/十 位隔期交替 → 预测下一期落对侧。
    两类触发分开统计：万类=d在万(T-6)与十(T-3)→预测万(T)；十类=d在十(T-6)与万(T-3)→预测十(T)。"""
    res = {"trig_w": 0, "hit_w": 0, "trig_s": 0, "hit_s": 0}
    for T in order:
        t6, t3 = str(int(T) - 6), str(int(T) - 3)
        if t6 not in draws or t3 not in draws:
            continue
        if draws[t6][0] == draws[t3][3]:
            res["trig_w"] += 1
            res["hit_w"] += (draws[T][0] == draws[t6][0])
        if draws[t6][3] == draws[t3][0]:
            res["trig_s"] += 1
            res["hit_s"] += (draws[T][3] == draws[t6][3])
    return res


def backtest_ap3(draws, order):
    """列内3连等差：某列连续3期 a,a+d,a+2d → 顺推 a+3d。"""
    res = {"trig": 0, "hit": 0}
    for T in order:
        t3, t2, t1 = str(int(T) - 3), str(int(T) - 2), str(int(T) - 1)
        if t3 not in draws or t2 not in draws or t1 not in draws:
            continue
        for pos in range(5):
            a, d1, d2 = draws[t3][pos], draws[t2][pos], draws[t1][pos]
            dd = d1 - a
            if dd != 0 and d1 == a + dd and d2 == a + 2 * dd:
                res["trig"] += 1
                res["hit"] += (draws[T][pos] == a + 3 * dd)
    return res


def run_family_backtests(draws):
    """对全部历史做三个家族回测，返回统计。"""
    order = sorted(draws.keys(), key=int)
    xcross = {str(g): backtest_xcross(draws, order, g) for g in (2, 3, 4, 5)}
    return {
        "xcross": xcross,
        "alternation": backtest_alternation(draws, order),
        "ap3": backtest_ap3(draws, order),
        "history_span": len(order),
    }


def fmt_rate(stats):
    """把家族统计转成可读文本。"""
    n = stats.get("trig", 0)
    if not n:
        return "历史从未触发，无法验证"
    hit = stats.get("hit_w", stats.get("hit", 0))
    denom = stats.get("denom", n)
    return f"触发{n}次,命中{hit}/{denom}={hit / denom:.0%}"


def compose_verdict(rule, fact_errs, fam, bt, target, actual):
    """按 事实→家族回测→随机对照 顺序给每条规律判语。"""
    if fact_errs:
        return "⚠️描述有误: " + ";".join(fact_errs)
    logic = rule.get("logic", "")
    if "xcross" in fam:
        g = "3"
        s = bt["xcross"].get(g, {"trig": 0})
        return (f"X形交叉(间隔{g}期){fmt_rate(s)} | 单码基准10%、2码20%"
                + (" | 样本不足,无法证实/证伪" if s["trig"] <= 2 else ""))
    if "alternation" in fam:
        s = bt["alternation"]
        nw, ns = s["trig_w"], s["trig_s"]
        wr = f"{s['hit_w']}/{nw}={s['hit_w'] / nw:.0%}" if nw else "—"
        sr = f"{s['hit_s']}/{ns}={s['hit_s'] / ns:.0%}" if ns else "—"
        return f"隔期摆动家族:万类{nw}次触发命中{wr},十类{ns}次触发命中{sr} | 单码基准10%"
    if "ap3" in fam:
        s = bt["ap3"]
        return f"3连等差家族{s['trig']}次触发,顺推命中{s['hit']}/{s['trig']}={s['hit'] / s['trig']:.0%} | 单码基准10%"
    # 主观取值
    exp = random_expect(rule)
    hit = actual
    rel = "高于随机" if hit > exp else ("约等于随机" if abs(hit - exp) < 0.5 else "低于随机")
    return (f"主观取值无法机械复现；候选{len(rule.get('predicted_positions', []))}位期望{exp}中{hit}({rel})，"
            f"仅1期样本，不能判定规律有效")


def verify_one(rule, draws, bt, target):
    """单条规律完整验证。"""
    fact_errs, refs = fact_check(rule, draws, target)
    mref, span = lookback_window(rule.get("logic", ""), target)
    fam = classify_family(rule.get("logic", ""))
    pc = rule.get("pos_check", {})
    cands = [{"position": p["位置"], "candidates": p["候选"],
              "hit": "✓" in pc.get(p["位置"], "")} for p in rule.get("predicted_positions", [])]
    actual = actual_hits(rule)
    return {
        "period": target,
        "blogger": rule.get("blogger"),
        "image": rule.get("image"),
        "family": [FAMILY_RULES[f]["label"] if f in FAMILY_RULES else "主观取值" for f in fam],
        "window": {"min_ref": mref, "target": target, "span": span,
                   "txt": f"{mref}–{target}（{span}期）" if mref else "—"},
        "fact_check": {"passed": not fact_errs, "errors": fact_errs,
                       "n_refs": len(refs)},
        "candidates": cands,
        "actual_hits": actual,
        "random_expect": random_expect(rule),
        "verdict": compose_verdict(rule, fact_errs, fam, bt, target, actual),
    }


def main():
    ap = argparse.ArgumentParser(description="规律验证（无未来函数回测）")
    ap.add_argument("--rules", required=True, help="规律库 JSON（docs/规律/<期>.json）")
    ap.add_argument("--draws", required=True, help="历史开奖 JSON（lottery_recent.json）")
    ap.add_argument("--out", help="验证结果输出 JSON")
    ap.add_argument("--md", help="验证结果输出 Markdown")
    ap.add_argument("--inplace", action="store_true", help="把 verify 字段写回规律库 JSON")
    args = ap.parse_args()

    lib = json.load(open(args.rules, encoding="utf-8"))
    draws = {x["period"]: x["numbers"] for x in json.load(open(args.draws, encoding="utf-8"))}
    target = lib["period"]

    bt = run_family_backtests(draws)
    results = [verify_one(r, draws, bt, target) for r in lib["rules"]]
    n_pass = sum(1 for v in results if v["fact_check"]["passed"])
    n_rules = len(results)

    report = {
        "period": target,
        "rules_total": n_rules,
        "fact_pass": n_pass,
        "family_backtests": bt,
        "rules": results,
        "note": "无未来函数：触发只用目标期之前数据；命中用目标期真值打分。规律只在回看窗口内有效。",
    }

    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        json.dump(report, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"[ok] 验证报告 → {args.out}")

    if args.inplace:
        for r, v in zip(lib["rules"], results):
            r["verify"] = v
        json.dump(lib, open(args.rules, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"[ok] verify 字段已写回 → {args.rules}")

    if args.md:
        lines = [f"# 规律验证 — {target} 期", "",
                 f"> 规律 {n_rules} 条 ｜ 结构事实通过 {n_pass} 条 ｜ 无未来函数滚动回测",
                 "", "| 博主 | 画法家族 | 回看窗口 | 事实核对 | 候选命中 | 验证判定 |", "|---|---|---|---|---|---|"]
        for r, v in zip(lib["rules"], results):
            fam = ",".join(v["family"]).replace("|", "｜")
            cand = " ".join(f"{c['position']}{','.join(map(str, c['candidates']))}{'✓' if c['hit'] else '✗'}"
                            for c in v["candidates"]).replace("|", "｜")
            fc = "✓" if v["fact_check"]["passed"] else "✗ " + ";".join(v["fact_check"]["errors"])
            lines.append(f"| {v['blogger']} | {fam} | {v['window']['txt']} | {fc} | {cand} | "
                         f"{v['verdict'].replace('|', '｜')} |")
        open(args.md, "w", encoding="utf-8").write("\n".join(lines))
        print(f"[ok] 验证 md → {args.md}")

    # 控制台摘要
    print(f"\n===== {target} 期规律验证摘要（{n_rules} 条）=====")
    for v in results:
        fc = "✓" if v["fact_check"]["passed"] else "✗"
        print(f"[{fc}] {v['blogger']:<16} {','.join(v['family']):<12} "
              f"{v['window']['txt']:<18} 期望{v['random_expect']}中{v['actual_hits']}  {v['verdict'][:50]}")


if __name__ == "__main__":
    main()
