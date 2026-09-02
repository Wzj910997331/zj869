#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成"人工核对目录"：把指定期命中规律对应的图 + prediction.json + 检查清单.md
归到 check_<期号>_hit/（git-ignored），供用户逐条核对命中是否真实。

prediction.json 每条规则含：博主/类型/预测位置/预测数字/实际开奖/命中说明/
规律说明(desc 识别原文)/图片类型。desc 从 image_patterns_with_blogger.json
按 (file,type,position,numbers) join 回填。

用法:
  python tools/make_check_dirs.py [--period 26231] [--period 26232] [--all]
"""
import argparse
import json
import os
import re
import shutil

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POS_NAME = ["万", "千", "百", "十", "个"]
PERIODS = {  # 期号 -> (base 日期, 开奖)
    "26231": ("20260829", [1, 8, 7, 9, 9]),
    "26232": ("20260830", [8, 0, 2, 3, 3]),
}


def sanitize(name):
    s = re.sub(r"[^\w一-鿿]+", "_", name).strip("_")[:30]
    return s or "anon"


def load_desc_index(base):
    """构建 (file,type,position,frozenset(numbers)) → desc 索引。"""
    recs = json.load(open(f"{REPO}/data/crawl/{base}/image_patterns_with_blogger.json", encoding="utf-8"))
    idx = {}
    for r in recs:
        key = (r.get("file"), r.get("type"), r.get("position"),
               frozenset(r.get("numbers") or []))
        idx[key] = (r.get("desc"), r.get("img_type"))
    return idx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", action="append", default=[])
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    targets = PERIODS if args.all else {p: PERIODS[p] for p in args.period}
    if not targets:
        print("请指定 --period 26231 / 26232，或 --all")
        return

    for period, (base, draw) in targets.items():
        rules = json.load(open(f"{REPO}/docs/规律/{period}.json", encoding="utf-8"))["rules"]
        rules_meta = json.load(open(f"{REPO}/docs/规律/{period}.json", encoding="utf-8"))
        img_dir = f"{REPO}/data/crawl/{base}/images"
        desc_idx = load_desc_index(base)
        out = f"{REPO}/check_{period}_hit"
        os.makedirs(out, exist_ok=True)
        for old in os.listdir(out):  # 清理旧图（保留 json/md 由本脚本重写）
            if not old.endswith((".json", ".md")):
                os.remove(os.path.join(out, old))

        seen = {}
        entries = []
        for i, r in enumerate(rules, 1):
            f = r["image"]
            src = os.path.join(img_dir, f)
            if f not in seen:
                dst = os.path.join(out, f"{len(seen)+1:03d}_{sanitize(r['blogger'])}_{f}")
                shutil.copy(src, dst)
                seen[f] = os.path.basename(dst)

            t, pos, nums = r.get("type"), r.get("hit_position"), r.get("hit_numbers") or []
            if t == "胆码":
                hit_pos = [POS_NAME[i] for i in range(5) if draw[i] in nums]
                hit_note = f"全盘命中: {','.join(hit_pos)} 位含候选数字" if hit_pos else "未命中"
            else:
                ip = POS_NAME.index(pos) if pos in POS_NAME else None
                hit_note = (f"{pos}位命中(实际{draw[ip]})" if ip is not None and draw[ip] in nums
                            else f"{pos}位未命中")
            # 规律说明：join 回识别记录的 desc
            desc, itype = desc_idx.get((f, t, pos, frozenset(nums)), (None, None))
            if desc is None:
                # position 字段名差异兜底（hit_position 可能比 position 短）
                for cand in desc_idx:
                    if cand[0] == f and cand[1] == t and set(cand[3]) == set(nums):
                        desc, itype = desc_idx[cand]
                        break
            entries.append({
                "规则号": i,
                "图": seen[f],
                "原图": f,
                "博主": r.get("blogger"),
                "类型": t,
                "预测位置": pos or "全盘",
                "预测数字": nums,
                "实际开奖": draw,
                "命中说明": hit_note,
                "规律说明": desc or "（无识别描述）",
                "图片类型": itype,
            })

        out_json = {
            "period": period,
            "draw": " ".join(map(str, draw)),
            "采集": rules_meta["total_records"],
            "规则数": len(entries),
            "图片数": len(seen),
            "rules": entries,
        }
        json.dump(out_json, open(f"{out}/prediction.json", "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)

        L = [f"# {period} 期命中规律检查清单", "",
             f"> 开奖 {out_json['draw']} ｜ 采集 {out_json['采集']} 条 ｜ 命中规律 {len(entries)} 条 / {len(seen)} 张图", "",
             "| # | 博主 | 类型 | 预测位置 | 预测数字 | 命中 | 规律说明 |",
             "|---|---|---|---|---|---|---|"]
        for e in entries:
            L.append(f"| {e['规则号']} | {e['博主']} | {e['类型']} | {e['预测位置']} | "
                     f"{','.join(map(str, e['预测数字']))} | {e['命中说明']} | {e['规律说明']} |")
        open(f"{out}/检查清单.md", "w", encoding="utf-8").write("\n".join(L))
        print(f"{period}: {len(entries)} 条 / {len(seen)} 张图 -> {out}/")


if __name__ == "__main__":
    main()
