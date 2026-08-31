# -*- coding: utf-8 -*-
"""画规方法库生成：为每位博主总结 26230 期的画图规律与推理逻辑。

输入:
  --records data/crawl/20260828/image_patterns_with_blogger.json   全部画图记录(48博主/704条)
  --draws   data/crawl/20260828/lottery_recent.json                开奖历史
  --verify  data/crawl/20260828/verify_results_final3.json         命中核验结论(可选)
输出:
  --out-json  data/crawl/20260828/pattern_methods.json
  --out-md    docs/画规方法库.md

说明: 用 deepseek-v4-flash 文本模型(无需读图)根据记录描述+核验结论+开奖历史合成"画规方法"。
目标: 累计1000条画规方法。
"""
import argparse
import concurrent.futures
import json
import os
import re
import sys
import time
import urllib.request

API_URL = "http://llm.riverbegin.cn/v1/chat/completions"
MODEL = "deepseek-v4-flash"


def load_api_key():
    k = os.environ.get("DEEPSEEK1_API_KEY")
    if k:
        return k.strip()
    home = os.environ.get("DSH_HOME") or os.path.join(os.path.expanduser("~"), ".dsh")
    cred = os.path.join(home, ".credentials.yaml")
    if os.path.exists(cred):
        for line in open(cred, encoding="utf-8"):
            if ":" in line:
                name, val = line.split(":", 1)
                if name.strip().strip("{}").strip() == "DEEPSEEK1_API_KEY":
                    return val.strip().strip("{}").strip()
    return None


def call_text(api_key, prompt, timeout=180):
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 3000,
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
    return data["choices"][0]["message"]["content"]


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


def build_prompt(blogger, recs, verdicts, draws_txt):
    rec_lines = []
    for i, r in enumerate(recs, 1):
        pos = r.get("position") or "无位次"
        rec_lines.append(
            f"{i}. 图:{r['file'][:40]} | 类型:{r['type']} | 位置:{pos} | 数字:{r.get('numbers')} | 描述:{r.get('desc','')}"
        )
    v_lines = []
    for v in verdicts:
        v_lines.append(
            f"- {v['verdict']}: {v['type']} {v['position']} {v['numbers']} ({v.get('note','')[:60]})"
        )
    return f"""你是彩票"走势图画规"分析方法论专家。下面是博主"{blogger}"在排列5第26230期开奖前发布的走势图画规记录（来自视觉识别）。

【开奖历史（万-千-百-十-个）】
{draws_txt}
【26230期开奖】9 4 6 8 3（万9 千4 百6 十8 个3）—— 博主预测的目标期

【博主"{blogger}"的画图记录（{len(recs)}条）】
{chr(10).join(rec_lines)}

【命中核验结论（部分记录已人工/机器复核）】
{chr(10).join(v_lines) if v_lines else "(无核验记录)"}

请总结该博主的"画规方法"，输出JSON：
1. method_type: 画法类型数组（从[斜连,直连,重号,邻号,遗漏,冷热,对称,框选胆码,定位,杀号,和值,尾数]选1-3个）
2. style: 一句话风格概括
3. description: 详细画法描述（引用记录里的圈选/连线/框选/手写内容，说明在哪个位置画了什么）
4. reasoning: 推理逻辑——博主为什么这么画、怎么从历史走势推导出预测（结合开奖历史具体分析，如"百位0连开两期后博主沿斜线在26230百位画6"；若记录显示是斜连/直连/重号等，说明连线的数字轨迹与推理）
5. predictions: 预测数组，元素为{{"position":"万位/千位/百位/十位/个位","digits":[数字],"hit":true/false/null}}（对照9,4,6,8,3；核验CONFIRM的记录hit填true，REJECT的记录仍列出但hit=false并注明，未核验且无法确定预测位的填null）
6. hit_summary: 命中情况一句话（如"定位2中1"或"无定位预测"）
7. method_summary: 可复用的方法要点（2-3句，讲清这个画法怎么用、适用条件）

只报告记录中真实存在的内容，不要编造。严格按JSON返回：{{"blogger":"{blogger}","method_type":[],"style":"","description":"","reasoning":"","predictions":[],"hit_summary":"","method_summary":""}}"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--records", required=True)
    ap.add_argument("--draws", required=True)
    ap.add_argument("--verify", default=None, help="核验结果json(可省略)")
    ap.add_argument("--out-json", default="data/crawl/20260828/pattern_methods.json")
    ap.add_argument("--out-md", default="docs/画规方法库.md")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0, help="只处理前N个博主(调试用)")
    ap.add_argument("--fallback-data", default=None,
                    help="数据初版pattern_methods.json：失败博主回退到其数据条目，保证总数完整")
    ap.add_argument("--model", default="deepseek-v4-flash", help="文本模型，可切换 glm-5.3 / glm-5.3-flash 等")
    args = ap.parse_args()
    sys.stdout.reconfigure(errors="replace")
    global MODEL
    MODEL = args.model

    api_key = load_api_key()
    if not api_key:
        print("找不到 DEEPSEEK1_API_KEY")
        sys.exit(1)

    # 数据初版回退表
    fallback_map = {}
    if args.fallback_data and os.path.exists(args.fallback_data):
        fd = json.load(open(args.fallback_data, encoding="utf-8"))
        for m in fd.get("methods", []):
            if "error" not in m:
                fallback_map[m["blogger"]] = m

    records = json.load(open(args.records, encoding="utf-8"))
    draws = json.load(open(args.draws, encoding="utf-8"))
    # 开奖历史文本（最近11期，新→旧）
    dmap = {d["period"]: d["numbers"] for d in draws}
    recent = sorted(dmap.keys(), reverse=True)[:11]
    draws_txt = "\n".join(f"{p}期 = {' '.join(map(str, dmap[p]))}" for p in recent)
    target = [9, 4, 6, 8, 3]
    draws_txt += f"\n26230期 = {' '.join(map(str, target))}  <- 目标期"

    # 核验结论按 博主+文件 索引
    verify_map = {}
    if args.verify and os.path.exists(args.verify):
        vd = json.load(open(args.verify, encoding="utf-8"))
        for v in vd.get("verdicts", []):
            verify_map.setdefault(v.get("blogger"), []).append(v)

    # 按博主分组
    by_blogger = {}
    for r in records:
        by_blogger.setdefault(r["blogger"], []).append(r)
    bloggers = sorted(by_blogger.keys())
    if args.limit:
        bloggers = bloggers[: args.limit]
    print(f"博主数: {len(bloggers)}")

    def one(blogger):
        recs = by_blogger[blogger]
        verdicts = verify_map.get(blogger, [])
        prompt = build_prompt(blogger, recs, verdicts, draws_txt)
        for attempt in range(6):
            try:
                raw = call_text(api_key, prompt)
                v = extract_json(raw)
                if v is None:
                    time.sleep(3 + attempt * 2)
                    if attempt == 5:
                        return blogger, None  # 失败→回退数据初版
                    continue
                v["blogger"] = blogger
                v["period"] = "26230"
                v["draw"] = "9 4 6 8 3"
                v["record_count"] = len(recs)
                v["image_files"] = sorted(set(r["file"] for r in recs))
                v["data_source"] = "llm"
                return blogger, v
            except Exception as e:
                time.sleep(3 * (attempt + 1))
                if attempt == 5:
                    return blogger, None
        return blogger, None

    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        for blogger, v in ex.map(one, bloggers):
            results[blogger] = v
            if v is None:
                print(f"  [FALLBACK] {blogger}")
            else:
                print(f"  [OK] {blogger}: {v.get('method_type')}")

    methods = []
    n_llm = 0
    for i, blogger in enumerate(bloggers, 1):
        v = results.get(blogger)
        if v is None:
            fb = fallback_map.get(blogger)
            if fb:
                fb = dict(fb)
                fb["method_id"] = f"M{i:04d}"
                fb["data_source"] = "data-v1(LLM未完成,网关故障)"
                methods.append(fb)
            else:
                methods.append({"method_id": f"M{i:04d}", "blogger": blogger, "period": "26230",
                                "error": "LLM失败且无数据回退"})
            continue
        v["method_id"] = f"M{i:04d}"
        methods.append(v)
        n_llm += 1

    os.makedirs(os.path.dirname(args.out_json), exist_ok=True)
    with open(args.out_json, "w", encoding="utf-8") as f:
        json.dump({"goal": "画规方法库,目标1000条", "period": "26230", "total": len(methods),
                   "methods": methods}, f, ensure_ascii=False, indent=1)

    # 生成 Markdown
    ok = [m for m in methods if "error" not in m]
    md = ["# 画规方法库（26230期）", "",
          f"> 目标：记录 1000 条画规方法。本期（26230，开奖 9 4 6 8 3）产出 {len(ok)} 条。",
          "", "| 方法ID | 博主 | 画法类型 | 风格 | 命中 |", "|---|---|---|---|---|"]
    for m in methods:
        if "error" in m:
            md.append(f"| {m['method_id']} | {m['blogger']} | - | 生成失败 | - |")
            continue
        md.append(f"| {m['method_id']} | {m['blogger']} | {'/'.join(m.get('method_type') or [])} | {m.get('style','')} | {m.get('hit_summary','')} |")
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
    os.makedirs(os.path.dirname(args.out_md), exist_ok=True)
    with open(args.out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    print(f"输出: {args.out_json} ({len(methods)}条, 其中LLM {n_llm}条) + {args.out_md}")
    print(f"成功 {len(ok)} / 失败 {len(methods)-len(ok)}")


if __name__ == "__main__":
    main()
