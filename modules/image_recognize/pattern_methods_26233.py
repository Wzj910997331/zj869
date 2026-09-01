#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pattern_methods_26233.py — 26233 期博主画规方法库(ds)。

复刻 20260828/pattern_methods.json 结构(26230那套)：
  输入:  data/crawl/20260831/vision_patterns_full.json   (ds 视觉识别博主画法标注)
        data/crawl/20260831/lottery_recent.json          开奖历史
  1) 本地核验: 每条画法标注 vs 26233=[1,6,3,4,0] 命中(common.run_hits)
  2) ds 文本(deepspeed-v4-flash)按博主总结 method_type/style/description/reasoning/predictions/hit_summary/method_summary
  输出:  data/crawl/20260831/pattern_methods.json + docs/画规方法库-26233.md

用法:
  /usr/bin/python3 modules/image_recognize/pattern_methods_26233.py \
      --date 20260831 --target-period 26233 --target-draw "1 6 3 4 0" [--workers 4]
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "modules", "image_recognize"))
from common import run_hits, load_json  # noqa: E402

API_URL = "http://llm.riverbegin.cn/v1/chat/completions"
MODEL = "deepseek-v4-flash"
TYPE_MAP = {"斜连": "斜连", "胆码": "框选胆码", "杀号": "杀号", "定位": "定位",
            "和值": "和值", "头": "定位(头)", "尾": "尾数"}


def load_api_key():
    k = os.environ.get("DEEPSEEK1_API_KEY")
    if k:
        return k
    for p in ("~/.dsh/.credentials.yaml", os.path.join(REPO, ".credentials.yaml")):
        p = os.path.expanduser(p)
        if os.path.exists(p):
            for line in open(p, encoding="utf-8"):
                if ":" in line:
                    name, val = line.split(":", 1)
                    if name.strip().strip("{}").strip() == "DEEPSEEK1_API_KEY":
                        return val.strip().strip("{}").strip()
    return None


def call_text(api_key, prompt, timeout=180):
    body = {"model": MODEL, "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3, "max_tokens": 3000}
    req = urllib.request.Request(API_URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + api_key})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())["choices"][0]["message"]["content"]


def extract_json(text):
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def pos2idx(pos):
    """画法 position(万/千/百/十/个或万位/第1位..) → 0-4；无法解析 → None。"""
    if not pos:
        return None
    if isinstance(pos, int):
        return pos if 0 <= pos <= 4 else None
    s = str(pos)
    if s in ("万", "千", "百", "十", "个"):
        return {"万": 0, "千": 1, "百": 2, "十": 3, "个": 4}[s]
    if "位" in s:
        if s.startswith("第"):
            d = re.search(r"(\d)", s)
            return int(d.group(1)) - 1 if d else None
        for k, v in {"万": 0, "千": 1, "百": 2, "十": 3, "个": 4}.items():
            if k in s:
                return v
    if "1位" in s or "首位" in s:
        return 0
    if "末位" in s or "5位" in s:
        return 4
    return None


def local_verify(patterns, draw):
    """对每条画法标注判命中(对齐 common.run_hits 口径)。斜连/无法定位的置 None。"""
    cands = []
    for p in patterns:
        nums = p.get("numbers") or []
        nums = [int(x) for x in nums if str(x).isdigit()]
        if not nums:
            cands.append(None)
            continue
        t = p.get("type") or "其他"
        idx = pos2idx(p.get("position"))
        if t in ("定位", "头", "尾", "斜连") and idx is None and t != "斜连":
            idx = None
        cands.append({"type": t, "position": idx, "numbers": nums})
    # 用 run_hits 算(斜连/无法定位 run_hits 可能 False)
    try:
        res = run_hits([c for c in cands if c], draw)
    except Exception:
        res = []
    out, ri = [], 0
    for c in cands:
        if c is None:
            out.append(None)
            continue
        if c["type"] == "斜连":
            # 斜连外推：末位数字出现在对应位置(或任一位置)视为命中
            hit = False
            if c["position"] is not None and nums:
                hit = draw[c["position"]] == nums[-1]
            out.append(hit)
            continue
        if ri < len(res):
            out.append(res[ri]["hit"])
        else:
            out.append(False)
        ri += 1
    return out


def build_prompt(blogger, recs, verified, draws_txt, target, draw):
    rec_lines = []
    for i, (r, hit) in enumerate(zip(recs, verified), 1):
        pos = r.get("position") or "无位次"
        h = "命中" if hit is True else ("未命中" if hit is False else "无法判定")
        rec_lines.append(f"{i}. 图:{r['file'][:44]} | 类型:{r.get('type')} | 位置:{pos} "
                         f"| 数字:{r.get('numbers')} | 描述:{r.get('desc','')} | 对{target}期: {h}")
    return f"""你是彩票"走势图画规"方法论专家。下面是博主"{blogger}"的走势图画规记录(视觉识别自 26233 期博主图)。

【开奖历史(万-千-百-十-个)】
{draws_txt}
【{target}期开奖】{' '.join(map(str, draw))}(万{draw[0]} 千{draw[1]} 百{draw[2]} 十{draw[3]} 个{draw[4]})

【博主"{blogger}"的画规记录({len(recs)}条,含对{target}期命中核验)】
{chr(10).join(rec_lines)}

请总结该博主的"画规方法"，输出JSON：
1. method_type: 画法类型数组(从[斜连,直连,重号,邻号,遗漏,冷热,对称,框选胆码,定位,杀号,和值,尾数]选1-3个)
2. style: 一句话风格概括
3. description: 详细画法描述(引用记录里的圈选/连线/框选/手写内容，说明在哪个位置画了什么)
4. reasoning: 推理逻辑——博主为什么这么画、怎么从历史走势推导出预测(结合开奖历史具体分析；若记录显示斜连/直连/重号，说明连线的数字轨迹与推理)
5. predictions: 预测数组，元素为{{"position":"万位/千位/百位/十位/个位","digits":[数字],"hit":true/false/null}}(对照{' '.join(map(str, draw))}；命中标true，未命中标false，无法判定位置标null)
6. hit_summary: 命中情况一句话(如"定位2中1"或"无定位预测")
7. method_summary: 可复用的方法要点(2-3句，讲清这个画法怎么用、适用条件)

只报告记录中真实存在的内容，不要编造。严格按JSON返回：{{"blogger":"{blogger}","method_type":[],"style":"","description":"","reasoning":"","predictions":[],"hit_summary":"","method_summary":""}}"""


def main():
    global MODEL
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="20260831")
    ap.add_argument("--target-period", default="26233")
    ap.add_argument("--target-draw", default="1 6 3 4 0")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0, help="只处理前N个博主(调试)")
    ap.add_argument("--model", default=MODEL)
    args = ap.parse_args()
    MODEL = args.model
    target_draw = [int(x) for x in args.target_draw.split()]

    api_key = load_api_key()
    if not api_key:
        print("找不到 DEEPSEEK1_API_KEY")
        sys.exit(1)

    base = os.path.join(REPO, "data", "crawl", args.date)
    vision = load_json(os.path.join(base, "vision_patterns_full.json"))
    draws = load_json(os.path.join(base, "lottery_recent.json"))
    dmap = {d["period"]: d["numbers"] for d in draws}
    recent = sorted(dmap.keys(), reverse=True)[:11]
    draws_txt = "\n".join(f"{p}期 = {' '.join(map(str, dmap[p]))}" for p in recent)

    # file -> blogger
    an = load_json(os.path.join(REPO, "data", "recognize", f"{args.date}_all",
                                "analysis", f"analyze_{args.date}.json"))["images"]
    file_blogger = {f: r.get("blogger") or "未知" for f, r in an.items()}

    by_blogger = defaultdict(list)
    for v in vision:
        if not (v.get("patterns") or []):
            continue
        b = file_blogger.get(v["file"]) or "未知"
        for p in v["patterns"]:
            if not p.get("numbers"):
                continue
            by_blogger[b].append({"file": v["file"], "type": p.get("type"),
                                  "position": p.get("position"),
                                  "numbers": p.get("numbers"), "desc": p.get("desc")})
    bloggers = sorted(by_blogger.keys())
    if args.limit:
        bloggers = bloggers[: args.limit]
    print(f"博主 {len(bloggers)} 位, 画法记录 "
          f"{sum(len(v) for v in by_blogger.values())} 条")

    def one(blogger):
        recs = by_blogger[blogger]
        verified = local_verify(recs, target_draw)
        prompt = build_prompt(blogger, recs, verified, draws_txt,
                              args.target_period, target_draw)
        for attempt in range(5):
            try:
                raw = call_text(api_key, prompt)
                v = extract_json(raw)
                if v is None:
                    time.sleep(3 + attempt * 2)
                    continue
                v["blogger"] = blogger
                v["period"] = args.target_period
                v["draw"] = " ".join(map(str, target_draw))
                v["record_count"] = len(recs)
                v["image_files"] = sorted(set(r["file"] for r in recs))
                v["data_source"] = "llm-ds"
                n_hit = sum(1 for h in verified if h is True)
                n_all = sum(1 for h in verified if h is not None)
                v["hit_summary"] = f"{n_hit}命中/{n_all}核验" if n_all else "无核验记录"
                return blogger, v
            except Exception as e:
                time.sleep(3 * (attempt + 1))
        return blogger, None

    results = {}
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(one, b): b for b in bloggers}
        for i, fut in enumerate(as_completed(futs), 1):
            b, v = fut.result()
            results[b] = v
            if v is None:
                print(f"  [{i}/{len(bloggers)}] ✗ {b}")
            else:
                print(f"  [{i}/{len(bloggers)}] ✓ {b}: {v['method_type']} {v['hit_summary']}")

    methods = []
    for i, b in enumerate(bloggers, 1):
        v = results.get(b)
        if v is None:
            methods.append({"method_id": f"M{i:04d}", "blogger": b, "period": args.target_period,
                            "error": "LLM失败"})
            continue
        v["method_id"] = f"M{i:04d}"
        methods.append(v)

    out = os.path.join(base, "pattern_methods.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"goal": "画规方法库,目标1000条", "period": args.target_period,
                   "draw": target_draw, "total": len(methods), "methods": methods},
                  f, ensure_ascii=False, indent=1)
    ok = [m for m in methods if "error" not in m]
    md = ["# 画规方法库（26233期）", "",
          f"> 26233 期开奖 = {' '.join(map(str, target_draw))} · 产出 {len(ok)} 条（ds 视觉+ds 文本）",
          "", "| 方法ID | 博主 | 画法类型 | 风格 | 命中 |", "|---|---|---|---|---|"]
    for m in methods:
        if "error" in m:
            md.append(f"| {m['method_id']} | {m['blogger']} | - | 生成失败 | - |")
            continue
        md.append(f"| {m['method_id']} | {m['blogger']} | {'/'.join(m.get('method_type') or [])} "
                  f"| {m.get('style','')} | {m.get('hit_summary','')} |")
    md += ["", "---", ""]
    for m in methods:
        if "error" in m:
            md += [f"## {m['method_id']} {m['blogger']}", f"> 生成失败: {m['error']}", ""]
            continue
        md += [f"## {m['method_id']} {m['blogger']}",
               f"- 画法类型: {'/'.join(m.get('method_type') or [])}",
               f"- 风格: {m.get('style','')}",
               f"- 画法描述: {m.get('description','')}",
               f"- 推理逻辑: {m.get('reasoning','')}",
               f"- 预测: {json.dumps(m.get('predictions',[]), ensure_ascii=False)}",
               f"- 命中: {m.get('hit_summary','')}",
               f"- 可复用要点: {m.get('method_summary','')}",
               f"- 图: {', '.join(m.get('image_files',[]))}", ""]
    out_md = os.path.join(REPO, "docs", f"画规方法库-{args.target_period}.md")
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"\n输出: {out} ({len(methods)}条, 成功 {len(ok)}) + {out_md}")


if __name__ == "__main__":
    main()
