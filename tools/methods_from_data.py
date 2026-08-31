# -*- coding: utf-8 -*-
"""画规方法库·数据初版：不依赖LLM，用已有记录+核验结论生成方法库（推理层待网关恢复后由 summarize_methods.py 补）。

每个博主一条方法：
- method_type: 从记录type映射（斜连/直连/重号/邻号/遗漏/冷热/对称/框选胆码/定位/杀号/和值/尾数）
- description: 汇集记录desc（画法原始描述）
- predictions/hit: 来自核验结论（CONFIRM/REJECT/KILL）
- reasoning: 留空(待LLM)

用法: python tools/methods_from_data.py --records ... --verify ... --out-json ... --out-md ...
"""
import argparse
import json
import os
import sys

TYPE_MAP = {
    "斜连": "斜连",
    "胆码": "框选胆码",
    "杀号": "杀号",
    "定位": "定位",
    "和值": "和值",
    "头": "定位(头)",
    "尾": "尾数",
}

REASON_TEMPLATES = {
    "斜连": "博主圈选历史数字作斜连/连线走势（如{ex}），沿趋势方向外推26230期落点。",
    "框选胆码": "博主以框选/圈选形式锁定候选胆码（如{ex}），按位置给出本期待选数字。",
    "定位": "博主对具体位置（{ex}）作定位标注，直接给出该位候选数字。",
    "杀号": "博主对历史走势分析后作杀号排除（如{ex}），排除/规避某些数字或位置。",
    "和值": "博主分析历史和值区间走势（如{ex}），给出和值方向判断。",
    "尾数": "博主判断个位尾数走势（如{ex}），给出尾数候选。",
}


def derive_reasoning(recs):
    """从记录类型+描述推导基础推理逻辑（LLM不可用时的兜底）。"""
    by_type = {}
    for r in recs:
        t = r.get("type") or "其他"
        by_type.setdefault(t, []).append(r)
    parts = []
    for t, rs in by_type.items():
        ex = rs[0].get("desc", "")[:24]
        tmpl = REASON_TEMPLATES.get(t)
        if tmpl:
            parts.append(tmpl.format(ex=ex or "数字圈选"))
    return "；".join(parts) if parts else "（记录未含明确画法描述）"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--records", required=True)
    ap.add_argument("--verify", required=True)
    ap.add_argument("--out-json", default="data/crawl/20260828/pattern_methods.json")
    ap.add_argument("--out-md", default="docs/画规方法库.md")
    ap.add_argument("--merge-llm", default=None,
                    help="含LLM推理条目的json：按blogger覆盖数据版条目（保留LLM质量）")
    args = ap.parse_args()
    sys.stdout.reconfigure(errors="replace")

    records = json.load(open(args.records, encoding="utf-8"))
    vd = json.load(open(args.verify, encoding="utf-8"))
    verdicts = vd["verdicts"]
    draw = vd.get("meta", {}).get("draw_26230") or [9, 4, 6, 8, 3]

    by_blogger = {}
    for r in records:
        by_blogger.setdefault(r["blogger"], []).append(r)
    by_blogger_v = {}
    for v in verdicts:
        by_blogger_v.setdefault(v["blogger"], []).append(v)

    methods = []
    for i, blogger in enumerate(sorted(by_blogger.keys()), 1):
        recs = by_blogger[blogger]
        vrecs = by_blogger_v.get(blogger, [])
        types = []
        for r in recs:
            t = TYPE_MAP.get(r.get("type"), r.get("type") or "其他")
            if t not in types:
                types.append(t)
        descs = [f"[{r.get('type')}|{r.get('position') or '无位次'}|{r.get('numbers')}] {r.get('desc','')}" for r in recs]
        confirms = [v for v in vrecs if v["verdict"] == "CONFIRM"]
        rejects = [v for v in vrecs if v["verdict"] == "REJECT"]
        kills = [v for v in vrecs if v["verdict"] in ("KILL", "NOPOS")]
        predictions = [
            {"position": v.get("position"), "digits": v.get("numbers"), "hit": True, "verdict": "CONFIRM",
             "note": v.get("note", "")[:80]}
            for v in confirms
        ] + [
            {"position": v.get("position"), "digits": v.get("numbers"), "hit": False, "verdict": "REJECT",
             "note": v.get("note", "")[:80]}
            for v in rejects
        ]
        n_hit = len(confirms)
        n_all = len(confirms) + len(rejects)
        methods.append({
            "method_id": f"M{i:04d}",
            "blogger": blogger,
            "period": "26230",
            "draw": " ".join(map(str, draw)),
            "method_type": types,
            "style": "，".join(types) + ("（核验" + str(n_hit) + "中" + str(n_all) + "）" if n_all else ""),
            "description": "；".join(descs[:12]),
            "reasoning": derive_reasoning(recs),
            "predictions": predictions,
            "hit_summary": f"{n_hit}命中/{n_all}核验" if n_all else "无核验记录",
            "method_summary": "",
            "image_files": sorted(set(r["file"] for r in recs)),
            "data_source": "data-v1",
        })

    # 合并LLM推理条目（按blogger覆盖）
    if args.merge_llm and os.path.exists(args.merge_llm):
        llm_map = {}
        for m in json.load(open(args.merge_llm, encoding="utf-8")).get("methods", []):
            if m.get("data_source") == "llm" and "error" not in m:
                llm_map[m["blogger"]] = m
        if llm_map:
            for m in methods:
                lm = llm_map.get(m["blogger"])
                if lm:
                    lm = dict(lm)
                    lm["method_id"] = m["method_id"]
                    methods[methods.index(m)] = lm
            print(f"合并LLM条目 {len(llm_map)} 条")

    os.makedirs(os.path.dirname(args.out_json), exist_ok=True)
    with open(args.out_json, "w", encoding="utf-8") as f:
        json.dump({"goal": "画规方法库,目标1000条", "period": "26230", "total": len(methods),
                   "draw": draw, "note": "数据初版，推理层待LLM补充", "methods": methods},
                  f, ensure_ascii=False, indent=1)

    md = ["# 画规方法库（26230期·数据初版）", "",
          f"> 目标：记录 1000 条画规方法。本期产出 {len(methods)} 条（数据驱动初版，推理层待 LLM 补充）。",
          "> 开奖：26230 = 9 4 6 8 3", "",
          "| 方法ID | 博主 | 画法类型 | 核验命中 |", "|---|---|---|---|"]
    for m in methods:
        md.append(f"| {m['method_id']} | {m['blogger']} | {'/'.join(m['method_type'])} | {m['hit_summary']} |")
    md += ["", "---", ""]
    for m in methods:
        md += [f"## {m['method_id']} {m['blogger']}",
               f"- 画法类型: {'/'.join(m['method_type'])}",
               f"- 画法描述: {m['description']}",
               f"- 预测核验: {json.dumps(m['predictions'], ensure_ascii=False)}",
               f"- 命中: {m['hit_summary']}",
               f"- 推理: {m['reasoning']}",
               f"- 图: {', '.join(m['image_files'][:6])}", ""]
    os.makedirs(os.path.dirname(args.out_md), exist_ok=True)
    with open(args.out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"输出 {len(methods)} 条: {args.out_json} + {args.out_md}")


if __name__ == "__main__":
    main()
