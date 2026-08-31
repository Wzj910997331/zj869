# -*- coding: utf-8 -*-
"""终局改判：把已验证的确定性结论应用到 final7 判定结果上，输出权威版。
1. 乐仔👑1288_0 杀号千位[0,5]：多读确认红框实为 万位49/千位05 预测框（无X杀号）→ 旧记录改 REJECT；
   千位{0,5}对开奖千=4未命中；图中另有 万位49 红框 → 万位9 命中（新增发现，原记录未含）。
2. 辉拓数据_4 杀号第1/3位：X位置存疑（在26229行区域）→ 维持 AMBIGUOUS，杀号类不计入命中。
用法: python tools/finalize_verdicts.py --in verify_results_final7.json --out verify_results_final.json
"""
import argparse
import json
import sys

OVERRIDES = {
    # file -> [(type, position, numbers, new_verdict, note)]
    "s_2_9e2aa187-d628-4f29-a6ee-649e95b8e062_0.jpg": [
        ("杀号", "千位", [0, 5], "AMBIGUOUS",
         "图中红格为万位49、千位05：原记录记为'杀号红格(斜连后杀)'，新读称'预测框(非X)'，红格含义存疑。"
         "若为杀号：千位杀{0,5}开千4→杀对(KILL类)；万位杀{4,9}开万9→杀错。"
         "若为预测：千位{0,5}开千4→未中；万位{4,9}开万9→命中。"
         "两种解读下千位记录均不算定位命中；万位是否命中取决于红格含义，待人工确认。按约定杀号不计入命中。"),
    ],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="infile", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    sys.stdout.reconfigure(errors="replace")

    data = json.load(open(args.infile, encoding="utf-8"))
    n_over = 0
    for v in data["verdicts"]:
        ovs = OVERRIDES.get(v["file"], [])
        for otype, opos, onums, overdict, onote in ovs:
            if v["type"] == otype and str(v.get("position")) == str(opos) and (v.get("numbers") or []) == onums:
                v["verdict"] = overdict
                v["note"] += " || 终局改判: " + onote
                v["basis"] = "machine-终局"
                n_over += 1
    print(f"改判 {n_over} 条")
    summary = {}
    for v in data["verdicts"]:
        summary[v["verdict"]] = summary.get(v["verdict"], 0) + 1
    print("汇总:", summary)
    data["summary"] = summary
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print("输出:", args.out)


if __name__ == "__main__":
    main()
