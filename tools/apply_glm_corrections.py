#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 glm_corrections_manifest.json 里的 GLM 修正重放到 image_patterns_with_blogger.json。
背景：本文件会被 summarize_image_patterns.py 重新生成覆盖，手动修正会丢。
本脚本从 manifest 重放（修改 13 + 删除 4 + 补改 1），可重复执行（幂等）。
输出: 重放后统计（总条数 / 命中 / 命中率，按类型）
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(REPO, "data", "crawl", "20260828")
PATH = os.path.join(BASE, "image_patterns_with_blogger.json")
MANIFEST = os.path.join(BASE, "glm_corrections_manifest.json")


def main():
    data = json.load(open(PATH, encoding="utf-8"))
    manifest = json.load(open(MANIFEST, encoding="utf-8"))

    applied, skipped = [], []

    # 1) index 匹配的修改（before 校验）
    for m in manifest:
        if "index" not in m:
            continue
        i, before, after = m["index"], m["before"], m["after"]
        if 0 <= i < len(data) and (
            data[i].get("file") == before.get("file")
            and data[i].get("type") == before.get("type")
            and data[i].get("position") == before.get("position")
            and data[i].get("numbers") == before.get("numbers")
        ):
            for k in ("type", "position", "numbers", "desc", "hit"):
                if k in after:
                    data[i][k] = after[k]
            data[i]["verified_by"] = after.get("verified_by", "glm-5.3-flash 2026-08-31")
            applied.append(i)
        else:
            skipped.append((i, before.get("file"), before.get("desc", "")[:30]))

    # 2) 删除辉拓 4 条空号杀号
    del_specs = []
    for m in manifest:
        if "deleted_records" in m:
            del_specs = m["deleted_records"]
    before_len = len(data)
    if del_specs:
        keys = {(d.get("file"), d.get("type"), d.get("position"), tuple(d.get("numbers") or []))
                for d in del_specs}
        data = [r for r in data if (r.get("file"), r.get("type"), r.get("position"),
                                    tuple(r.get("numbers") or [])) not in keys]

    # 3) 补改：微时光_1 [104] 红圈 个位3 → 十位3 hit=False
    for r in data:
        if (r.get("file", "").endswith("_1.jpg")
                and r.get("type") == "定位" and r.get("position") == "个位"
                and r.get("numbers") == [3] and "红圈" in (r.get("desc") or "")):
            r["position"] = "十位"
            r["hit"] = False
            r["desc"] = "红圈标注26230期十位3（GLM复核：原读个位，校准行法实为十位）"
            r["verified_by"] = "glm-5.3-flash 2026-08-31"
            applied.append("note104")
            break

    json.dump(data, open(PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # ---- 统计 ----
    from collections import Counter, defaultdict
    nh = sum(1 for r in data if r.get("hit"))
    print(f"应用修改 {len(applied)} 处（index 清单: {[a for a in applied if a!='note104']}{' + note104(微时光个位3→十位3)' if 'note104' in applied else ''}）")
    print(f"删除记录 {before_len - len(data)} 条（辉拓空号杀号）")
    print(f"最终条数: {len(data)} | 命中 26230: {nh} ({nh/len(data):.2%})")
    print("类型分布: ", dict(Counter(r.get('type') for r in data)))
    by = defaultdict(lambda: [0, 0])
    for r in data:
        by[r.get("type")][0] += 1
        if r.get("hit"):
            by[r.get("type")][1] += 1
    for t, (c, h) in sorted(by.items(), key=lambda kv: -kv[1][0]):
        print(f"  {t}: {c} 命中{h} ({h/c:.0%})")
    if skipped:
        print("跳过(未匹配): ", skipped)


if __name__ == "__main__":
    main()
