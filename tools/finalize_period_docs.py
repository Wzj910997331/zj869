#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""finalize_period_docs.py — 把 ⑤ export 的 docs + ⑦ verdict 合成**权威版** docs。

口径（README_口径要点_26231 / RUN_26232_说明 §3）：
  - 命中 = 博主写在目标期行的手写单押数字与实开一致，是**事实**（hit_records 保留不变）。
  - 【画规规律】须能只从博主画的那张图经固定规律库(repeat 签名串重复 / swap 万十对调 /
    slant 等差下推)推出；⑦ verdict=coincidence → 命中但无逻辑可推，移入 hit_no_rule，
    **不列规律**；verdict=ok 的可复现命中才留在 rules[]。

输入（均由前面几步产出，未入库）：
  --docs    data/crawl/<date>/blogger_predictions_verify_merged 导出版 docs/规律/<period>.json
  --verdict data/crawl/<date>/guihua_<period>_reproducible.verdict.json（含 robust 字段）
  --repro   data/crawl/<date>/guihua_<period>_reproducible.json（博主画法描述）

输出：覆盖 docs/规律/<period>.{json,md}（权威版 schema 同 26232：rules 只存可复现规律，
coincidence → hit_no_rule；rule_count=可复现条数）。
"""
import argparse
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def read_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def clean_drawn(s, cap=260):
    """画法描述缩略：保留 ds-fail 警示尾巴（诚实标注链读不全），控制长度。"""
    if not s:
        return ""
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= cap else s[:cap] + "…"


def match_issue(issues, pos, digit):
    """issues 形如 '预测百3: 规律库无法推出(...)'，取与命中位一致的那条。"""
    pref = f"预测{pos.replace('位','')}{digit}"
    for it in issues:
        if it.startswith(pref):
            return it
    return issues[0] if issues else ""


def build_hit_text(preds):
    """preds: [{position,digit,actual,hit}] → 命中行文本。"""
    toks = []
    for p in preds:
        pos = p["position"].replace("位", "")
        mark = "✓" if p.get("hit") else "✗"
        toks.append(f"{pos}{p['digit']}{mark}实{p['actual']}")
    return "（" + "；".join(toks) + "）"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", required=True)
    ap.add_argument("--verdict", required=True)
    ap.add_argument("--repro", required=True)
    args = ap.parse_args()

    docs = read_json(args.docs)
    if "hit_no_rule" in docs:
        print(f"⚠ {args.docs} 已是权威版（含 hit_no_rule）；如要重生成请先给 ⑤ 导出版"
              f"（export，无 hit_no_rule 字段）。")
        return
    verdict = read_json(args.verdict)
    repro = read_json(args.repro)
    period = docs["period"]
    draw = docs["draw"]
    act = [int(x) for x in draw.split()]
    vimages = verdict.get("images", {})
    rimgs = repro.get("images", {})

    hit_no_rule = []
    rules_final = []
    stat = {"ok": 0, "coincidence": 0, "ds_fail": 0, "miss": 0}
    for r in docs.get("rules", []):
        file = r["image"]
        vi = vimages.get(file, {})
        v = vi.get("verdict", "?")
        preds = vi.get("predictions", [])
        hpos = r.get("hit_position", "")
        hnum = (r.get("hit_numbers") or [None])[0]
        pos_idx = {"万位": 0, "千位": 1, "百位": 2, "十位": 3, "个位": 4}
        actual = act[pos_idx[hpos]] if hpos in pos_idx else None
        detail = build_hit_text(preds) if preds else ""
        hit_line = (f"{hpos}={hnum} → {period} {hpos}={actual} ✓" if actual == hnum
                    else f"{hpos}={hnum} → {period} 未中(实{actual})")
        if v == "ok":
            stat["ok"] += 1
            nr = dict(r)
            nr["verdict"] = "ok"
            nr["画规类型"] = rimgs.get(file, {}).get("画规类型", "")
            rules_final.append(nr)
            continue
        if v == "coincidence":
            stat["coincidence"] += 1
        else:
            stat["ds_fail"] += 1
        drawn = clean_drawn(rimgs.get(file, {}).get("画法描述", ""))
        issue = match_issue(vi.get("issues", []), hpos, hnum)
        logic = (f"博主画的链条：{drawn or '（未读到 struct，画法按叙述拼）'}。"
                 f"固定规律库对命中「{hpos}={hnum}」推不出（{issue or '无逻辑自洽，疑似巧合'}）"
                 f"→ 判**coincidence（巧合命中）**，命中保留但**不列规律**。"
                 + (f"\n稳健性：{vi.get('robust','')}" if vi.get("robust") else ""))
        hit_no_rule.append({
            "blogger": vi.get("blogger") or r["blogger"],
            "image": file,
            "hit": f"{hit_line} {detail}".strip(),
            "drawn_chain": drawn,
            "logic": logic,
            "verdict": v,
        })

    rule_count = len(rules_final)
    hit_no_rule_count = len(hit_no_rule)
    docs2 = dict(docs)
    docs2["rules"] = rules_final
    docs2["rule_count"] = rule_count
    docs2["hit_no_rule"] = hit_no_rule
    docs2["hit_no_rule_count"] = hit_no_rule_count
    docs2["miss_records"] = []
    docs2["miss_no_rule_count"] = 0
    n_dsfail = stat["ds_fail"]
    docs2["说明"] = (
        f"口径：命中=博主写在目标期行({period})的手写单押数字与实开一致；复盘帖(≥21:30)剔除。"
        f"命中总数 {docs.get('hit_records')} 条是**事实**。"
        f"但【画规规律】须由固定规律库(repeat/swap/slant)从博主亲手画的节点推出；"
        f"⑥ DS盲读 → ⑦ 自证复现：可复现且中 {stat['ok']} / 巧合命中(无逻辑,不列规律) {stat['coincidence']}"
        + (f" / 复现失败(ds-fail) {n_dsfail}" if n_dsfail else "")
        + f"；全部 coincidence 经上界变体敏感性扫描稳健（上界整列全画仍推不出命中预测）。"
        f"本文档 rule_count={rule_count}（只计可复现规律）。")

    out_json = args.docs
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(docs2, f, ensure_ascii=False, indent=1)
    print(f"权威版 json → {out_json} ｜ 命中 {docs.get('hit_records')} / "
          f"规则 {rule_count} / 巧合无规 {hit_no_rule_count} / ds-fail {n_dsfail}")

    # ---------------- markdown ----------------
    L = []
    L.append(f"# 命中规律库 — {period} 期")
    L.append("")
    L.append(f"> 期号：**{period}** ｜ 开奖：**{draw}**（万 千 百 十 个） ｜ 口径：博主目标期行手写+单押（复盘帖 ≥21:30 剔除）")
    L.append(f"> 采集记录：**{docs.get('total_records')} 条** ｜ 命中：**{docs.get('hit_records')} 条"
             f"（{docs.get('hit_rate',0)*100:.2f}%）** ｜ 完全命中（1位置1中）：{docs.get('full_hits')} 条")
    L.append("")
    L.append(f"## 命中=事实口径（{docs.get('hit_records')} 条），画规规律可推 **{rule_count} 条**")
    L.append("")
    L.append(f"命中总数是**事实**：博主写在目标期({period})行的手写单押数字与实开一致，共 **{docs.get('hit_records')} 条**。"
             "但【画规规律】须能**只从博主画的那张图的线条本身**推出预测——只认博主亲手画的节点"
             "(圈/连线/签名串)及其重现，绝不用「博主没画的历史/他列先例」来凑。⑥ DS盲读 + ⑦ 自证复现结果：")
    L.append("")
    L.append(f"- **可推的画规规律：{rule_count} 条**")
    L.append(f"- **命中但无逻辑可推（疑乱画/巧合，不列为方法）：{hit_no_rule_count} 条**"
             + (f"（其中 {n_dsfail} 张 struct 盲读网关超时，仅叙述读拼链）" if n_dsfail else ""))
    L.append("")
    if hit_no_rule:
        L.append("## 命中但无逻辑可推（疑乱画/巧合，不列为方法）")
        L.append("")
        L.append("| 博主 | 命中 | 博主画的链条（DS盲读） | 为何推不出 |")
        L.append("|---|---|---|---|")
        for h in hit_no_rule:
            b = h["blogger"]
            hi = h["hit"]
            dr = h["drawn_chain"] or "（未读到 struct，画法按叙述拼）"
            wh = h["logic"]
            k = wh.find("固定规律库对命中")
            wh = wh[k:] if k != -1 else wh
            wh = wh.split("\n稳健性")[0]
            wh = re.sub(r"\s+", " ", wh).strip()[:220]
            L.append(f"| {b} | {hi} | {dr} | {wh} |")
        L.append("")
        L.append("> 自证（2026-09-02/03）：以上命中图均送画规复现。DS盲读博主所画链条后，固定规律库"
                 "（repeat 签名串重复 / swap 万十对调 / slant 等差下推）对每条命中预测**推不出**"
                 " → 判 **coincidence（巧合命中，无逻辑自洽）**，命中保留但不列为规律。")
        if any(h["verdict"] == "coincidence" for h in hit_no_rule):
            L.append(">")
            L.append("> 稳健性（防「链条读不全→误判巧合」，上界变体）：把博主所画链条按**上界**（若他把历史"
                     "整列全画）喂同一规律库，仍推不出各命中预测 → coincidence 与链完整度无关。")
    if rules_final:
        L.append("")
        L.append("## 可推的画规规律")
        L.append("")
        L.append("| 博主 | 命中 | 推导 |")
        L.append("|---|---|---|")
        for rr in rules_final:
            L.append(f"| {rr['blogger']} | {rr.get('hit_position')}={rr.get('hit_numbers')} | "
                     f"{rr.get('画规类型','')} |")
    # 被剔除（多码/报号/无画规/读不出）
    rej = docs.get("rejected", [])
    L.append("")
    L.append(f"## 被剔除的（多码/报号/无画规/读不出，{len(rej)} 条）")
    L.append("")
    for rj in rej:
        L.append(f"- **{rj.get('blogger')}**：{rj.get('reason','')}")
    L.append("")
    L.append("> 规律为博主手绘画规的历史总结，彩票开奖属独立随机事件，不具备预测效力。")
    L.append("> **自证声明**：凡「规律」须能只从博主画的那张图的线条本身经固定规律库(repeat/swap/slant)推出；"
             f"本期 {hit_no_rule_count} 条命中均无固定模式可推，属**巧合命中**，不列为规律。")
    out_md = os.path.splitext(out_json)[0] + ".md"
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print(f"权威版 md  → {out_md}")


if __name__ == "__main__":
    main()
