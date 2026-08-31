# -*- coding: utf-8 -*-
"""复核第三阶段：对双读不一致(AMBIGUOUS)或失败(ERROR)的图，用 x 坐标精确判列再读一次。

用法:
  python tools/verify_phase2.py \
      --in  data/crawl/20260828/verify_results.json \
      --images "C:\\Users\\zhenjie.wu\\.dsh\\work\\gouli_jpg" \
      --crops  "C:\\Users\\zhenjie.wu\\.dsh\\work\\gouli_crop\\batch45" \
      --out data/crawl/20260828/verify_results_final.json \
      --workers 4

判定: 对每张需复核的图再读1次(x坐标提示)，与已有读取结果多数表决：
      任一判定(CONFIRM/REJECT/KILL/NOPOS)出现2次 → 采纳；否则保持 AMBIGUOUS。
"""
import argparse
import base64
import concurrent.futures
import importlib.util
import json
import os
import re
import sys
import time
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
API_URL = "http://llm.riverbegin.cn/v1/chat/completions"
MODEL = "deepseek-v4-flash-vision-exp"
DRAW_26230 = [9, 4, 6, 8, 3]
POS_MAP = {"万位": 1, "千位": 2, "百位": 3, "十位": 4, "个位": 5}

sys.path.insert(0, SCRIPT_DIR)
from verify_chart_hits import (  # noqa: E402
    build_prompt, call_vision, extract_json, judge_record, load_api_key, pos_to_col,
)

X_PROMPT_EXTRA = (
    "本次特别任务：用 x 坐标精确核对位名。请以校准行26229（或26228）的数字格横向位置为坐标参照，"
    "对26230预测行每个数字：报告其中心x坐标，并说明它与校准行哪个位（万/千/百/十/个）对齐"
    "（例：'数字6中心x≈435，与校准行万位2的x≈445同列'）。列边界附近的数字务必给出依据。"
    "position_digits 按你最有把握的位名填，x_evidence 字段写详细坐标对照。"
)


def build_x_prompt(filename):
    base = build_prompt(filename, X_PROMPT_EXTRA)
    base = base[:-2]  # 去掉末尾 "}}"，改为包含 x_evidence 的 schema
    return base + ',"x_evidence":"..."}}'


def majority(verdicts):
    """verdicts: [str...]，返回多数结论；AMBIGUOUS/ERROR 弃权。"""
    votes = [v for v in verdicts if v in ("CONFIRM", "REJECT", "KILL", "NOPOS")]
    for v in set(votes):
        if votes.count(v) >= 2:
            return v
    return "AMBIGUOUS"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="infile", required=True)
    ap.add_argument("--images", default=r"C:\Users\zhenjie.wu\.dsh\work\gouli_jpg")
    ap.add_argument("--crops", default=r"C:\Users\zhenjie.wu\.dsh\work\gouli_crop\batch45")
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--force", action="store_true", help="全部图都再读一次（不只看AMBIGUOUS/ERROR）")
    ap.add_argument("--model", default="deepseek-v4-flash-vision-exp", help="视觉模型，可切换 glm-5.3-flash")
    args = ap.parse_args()

    import verify_chart_hits as vch
    vch.MODEL = args.model

    api_key = load_api_key()
    if not api_key:
        print("找不到 DEEPSEEK1_API_KEY")
        sys.exit(1)

    data = json.load(open(args.infile, encoding="utf-8"))
    per_image = data["per_image"]
    verdicts = data["verdicts"]
    sys.stdout.reconfigure(errors="replace")

    # 需要复核的文件
    need = {}
    for v in verdicts:
        if v["verdict"] in ("AMBIGUOUS", "ERROR") or args.force:
            need.setdefault(v["file"], []).append(v)
    files = sorted(need.keys())
    if not files:
        print("无需复核的记录")
        sys.exit(0)
    print(f"需复核文件 {len(files)} 张")

    def one(f):
        crop_path = os.path.join(args.crops, f.replace(".jpg", "_b45.jpg"))
        if not os.path.exists(crop_path):
            crop_path = os.path.join(args.crops, f.replace(".jpg", "_full.jpg"))
        if not os.path.exists(crop_path):
            return f, [{"error": "裁剪图不存在: " + crop_path}]
        # ERROR 文件没有可用读取 → 需再读2次(x坐标+标准)；AMBIGUOUS 只补1次x读
        good = [r for r in per_image.get(f, {}).get("reads", [])
                if "error" not in r and r.get("calibration_ok")]
        need_reads = 2 - len(good)
        if need_reads <= 0:
            need_reads = 1  # AMBIGUOUS: 至少补1次x读做表决
        out = []
        prompts = [build_x_prompt(f)] + [build_prompt(f, X_PROMPT_EXTRA)] * 2
        for i in range(need_reads):
            for attempt in range(6):
                try:
                    raw = call_vision(api_key, crop_path, prompts[i], timeout=600)
                    v = extract_json(raw)
                    if v is None:
                        # 空响应：快速重试（网关间歇性返回空），不长退避
                        time.sleep(2 + attempt)
                        if attempt == 5:
                            out.append({"error": f"补读{i+1}失败: 6次空响应"})
                        continue
                    out.append(v)
                    break
                except urllib.error.HTTPError as e:
                    # 真正的HTTP错误（限流/5xx）：退避重试
                    time.sleep(10 * (attempt + 1))
                    if attempt == 5:
                        out.append({"error": f"补读{i+1}失败: HTTP {e.code}"})
                except Exception as e:
                    time.sleep(5 * (attempt + 1))
                    if attempt == 5:
                        out.append({"error": f"补读{i+1}失败: {e}"})
        if not out:
            return f, [{"error": "无读取结果"}]
        return f, out

    x_reads = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        for f, new_reads in ex.map(one, files):
            x_reads[f] = new_reads
            n_ok = sum(1 for r in new_reads if "error" not in r)
            print(f"  phase2 {f}: {n_ok} 次补读成功")

    # 汇总多数表决
    for v in verdicts:
        f = v["file"]
        if v["verdict"] not in ("AMBIGUOUS", "ERROR") and not args.force:
            continue
        new_reads = x_reads.get(f) or []
        good_new = [r for r in new_reads if "error" not in r]
        if not good_new:
            v["verdict"] = "ERROR"
            v["note"] += " || phase2补读全部失败"
            v["basis"] = "machine-3读"
            continue
        # 已有读取 + 补读
        reads = [r for r in per_image.get(f, {}).get("reads", []) if "error" not in r]
        all_reads = reads + good_new
        v1s = [judge_record(v, r)[0] for r in all_reads]  # 各读判定
        final = majority(v1s)
        old_note = v["note"]
        x_ev = " ".join((r.get("x_evidence") or "") for r in good_new)
        v["verdict"] = final
        v["note"] = f"{old_note} || 补读判定: {v1s[len(reads):]} [{x_ev[:120]}]"
        v["basis"] = "machine-3读"
        # 记录补读结果
        per_image.setdefault(f, {}).setdefault("reads", []).extend(good_new)

    summary = {}
    for v in verdicts:
        summary[v["verdict"]] = summary.get(v["verdict"], 0) + 1
    print("=== 最终判定汇总 ===", summary)

    data["summary"] = summary
    data["meta"]["rule"] = "双读一致或3读多数"
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print("输出:", args.out)
    for v in verdicts:
        print(f"  [{v['verdict']:9s}] {v['blogger']} | {v['file'][:22]} | {v['type']} {v['position']} {v['numbers']} | {v['note'][:100]}")


if __name__ == "__main__":
    main()
