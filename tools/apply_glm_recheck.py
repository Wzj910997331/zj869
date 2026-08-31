#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全量 GLM 重核 33 张"命中"图后的修正脚本。
GLM 逐张重读（校准行法）确认：20 张真命中、13 张假命中（列错位/胆码误读/空心框/和值误读）。
本脚本按 index 应用修正（13 条 hit→False + 2 条命中记录位置/类型修正），并加 verified_by 标记。
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(REPO, "data", "crawl", "20260828", "image_patterns_with_blogger.json")
VBY = "glm-5.3-flash 全量重核 2026-08-31"

# index -> {field: newvalue, desc: newdesc}
FIXES = {
    6: dict(type="定位", position="百位", desc="26230期百位候选1/6（GLM重核：百位双选框1/6，实际百位6命中）"),
    24: dict(type="定位", position="千位", hit=False, desc="26230期千位候选1/6（GLM重核：绿框1/6画在千位，实际千位4未中；原记胆码第3位误判）"),
    170: dict(position="千位", hit=False, desc="26230期千位预测6（GLM重核：6圈在千位非百位，实际千位4未中）"),
    176: dict(position="百位", numbers=[1], hit=False, desc="26230期百位预测1（GLM重核：百位标注1非6，实际百位6未中；万0千6十4个4/9亦未中）"),
    196: dict(type="定位", position="万位", numbers=[4], hit=False, desc="26230期万位青圈4、十位青圈3（GLM重核：位置有圈，实际万位9/十位8均未中；原记胆码4,3误判）"),
    198: dict(type="和值", position="千位", numbers=[5], hit=False, desc="26230期千+百位合5（GLM重核：空心圈+手写合5，实际千4+百6=10未中；原记胆码4,3是空圈误读）"),
    214: dict(position="百位", hit=False, desc="26230期百位圈8、个位圈2（GLM重核：8在百位非十位，实际百位6未中）"),
    222: dict(position="百位", hit=False, desc="26230期千位9、百位8、十位2（GLM重核：8在百位非十位，实际百位6未中）"),
    232: dict(position="千位", hit=False, desc="26230期千位6、百位3（GLM重核：6在千位非百位，实际千位4未中）"),
    349: dict(position="十位", hit=False, desc="26230期无标注，3/8画在26229行十位（GLM重核：26230行无预测标注，不计命中）"),
    408: dict(position="万位", numbers=[4, 9], desc="万位'4++3'防'9++8'、十位3/8（GLM重核：万位4防9命中万位9、十位3防8命中十位8；原记千位4误判）"),
    454: dict(type="定位", position="十位", hit=False, desc="26230期十位红圈3（GLM重核：3在十位非个位，实际十位8未中；原记胆码个位误判）"),
    490: dict(numbers=[], hit=False, desc="26230期万/千位空橙框无数字（GLM重核：框内无数字，原[4,9,8,9,1,2,5,2]是万位列历史数字，不计命中）"),
    542: dict(type="定位", position="千位", hit=False, desc="26230期千位绿圈6（GLM重核：6在千位非百位，实际千位4未中）"),
    549: dict(type="定位", position="千位", hit=False, desc="26230期千位实心圈9（GLM重核：9在千位非万位，实际千位4未中）"),
}


def main():
    data = json.load(open(PATH, encoding="utf-8"))
    applied, kept = [], []
    for idx, fix in FIXES.items():
        r = data[idx]
        for k, v in fix.items():
            r[k] = v
        r["verified_by"] = VBY
        applied.append(idx)
    for i, r in enumerate(data):
        if r.get("hit") and r.get("verified_by") == VBY and i not in FIXES:
            kept.append(i)  # GLM 确认的真命中
    json.dump(data, open(PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # 统计
    hits = [r for r in data if r.get("hit") and not (r.get("type") == "杀号" and not r.get("numbers"))]
    print(f"修正 {len(applied)} 条 → {applied}")
    print(f"GLM 确认保留命中: {len(kept)} 条 → {kept}")
    print(f"修正后总条数 {len(data)} | 真实命中 {len(hits)} ({len(hits)/len(data):.2%})")


if __name__ == "__main__":
    main()
