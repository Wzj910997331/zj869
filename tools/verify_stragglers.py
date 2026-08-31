# -*- coding: utf-8 -*-
"""尾部清理v2：对 ERROR/AMBIGUOUS 记录 + 指定复测文件，用专用裁剪+专用提示词再读（最多3次，多数表决）。
带列位对齐协议：先读26229/26228行5数字与真实开奖逐位比对，一致才算对齐。

用法:
  python tools/verify_stragglers.py \
      --in data/crawl/20260828/verify_results_final3.json \
      --crops "C:\\Users\\zhenjie.wu\\.dsh\\work\\gouli_crop" \
      --out data/crawl/20260828/verify_results_final5.json \
      --extra-files s_2_0f08b4cd-..._1.jpg,s_2_d4ab1b42-..._3.jpg \
      --workers 5
"""
import argparse
import concurrent.futures
import json
import os
import sys
import time
import urllib
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_chart_hits import (  # noqa: E402
    build_prompt, call_vision, extract_json, judge_record, load_api_key,
)
from verify_phase2 import majority  # noqa: E402

# file -> (专用裁剪后缀, 提示词类型)
SPECIAL = {
    "s_2_3cba6235-abbd-4ea9-90fb-0f0339be9635_1.jpg": ("_w0.55.jpg", "standard"),
    "s_2_3cba6235-abbd-4ea9-90fb-0f0339be9635_3.jpg": ("_w0.55.jpg", "standard"),
    "s_2_5632a784-21bc-48ce-a9dc-34ca55c6fdf1_6.jpg": ("_w0.55.jpg", "standard"),
    "s_2_38b63c64-6d6e-4aee-aeab-6e79cea99898_0.jpg": ("_w0.55.jpg", "standard"),
    "s_2_040f42e9-0147-4788-bf80-ba959a321706_4.jpg": ("_w0.55.jpg", "kill"),
    "s_2_9e2aa187-d628-4f29-a6ee-649e95b8e062_0.jpg": ("_w0.60.jpg", "kill"),
    "s_2_7f1d7d42-ccb5-4540-a765-e713bdb3027f_0.jpg": ("_full.jpg", "tail"),
    # 争议图复测（用b45标准裁剪）
    "s_2_0f08b4cd-f29d-44da-86b7-fed91bd56ce4_1.jpg": ("_b45.jpg", "standard"),
    "s_2_d4ab1b42-3697-44c1-988c-5aced97b1fec_3.jpg": ("_b45.jpg", "standard"),
}

ALIGN = """【列位对齐协议——必须严格执行】
1. 找到最近一期已开奖行 26229（不在图中则用 26228）。
2. 从左到右读出该行的【开奖数字】（跳过期号列、日期列、辅助数值列如13/15）。
3. 读出的数字必须依次等于真实开奖：26229 = 2,8,0,5,4（26228 = 5,6,0,2,5）。完全一致才算对齐成功，这5列从左到右即 万-千-百-十-个。
4. 若读出的不是 2,8,0,5,4，说明列没对齐（辅助列被当成开奖列/漏列）——重新定位直到一致。
5. calibration_row 填 "26229" 或 "26228"；calibration_digits 填最终读到的5个数字；calibration_ok 仅当完全一致时 true。"""

KILL_PROMPT = """你是图片分析专家。请用 read_image 工具读取图片：{path}
这是"排列五走势图"底部区域。真实开奖：26228=5,6,0,2,5；26229=2,8,0,5,4；26230=9,4,6,8,3（万-千-百-十-个）。
{ALIGN}
【位名锚定】万位=校准行数字2(或26228的5)所在列，千位=8(或6)，百位=0，十位=5(或2)，个位=4(或5)。

请重点寻找博主画的【杀号X标记/划痕】（打叉、斜线划掉数字等）：
1. 先执行列位对齐协议，确认位名锚定。
2. 找出图中所有X/划线杀号标记：每个标记在哪个期号行、哪个位？被杀的数字是什么？kill_positions 填位名数组如["万","千"]。
3. kill_entries 逐条列出，如"26230行千位: X杀0、5" 或 "26229行万位: X杀2"。
4. position_digits 仍按26230预测行的预测数字按位名填写（无预测留空数组）。
5. annotations_text 说明任何异常（如X标记跨越行边界）。
按JSON返回：{{"filename":"...","rows_visible":"...","calibration_row":"26229","calibration_digits":[],"calibration_ok":true,"calibration_detail":"...","position_digits":{{"万":[],"千":[],"百":[],"十":[],"个":[]}},"kill_entries":[],"kill_positions":[],"annotations_text":"...","position_confidence":"...","verdict":"...","x_evidence":"..."}}"""

TAIL_PROMPT = """你是图片分析专家。请用 read_image 工具读取图片：{path}
这是"排列五走势图"底部区域（可能是尾数走势或标准走势图）。真实开奖：26228=5,6,0,2,5；26229=2,8,0,5,4；26230=9,4,6,8,3（万-千-百-十-个），26230的个位(尾数)=3。
{ALIGN}
【位名锚定】万位=校准行数字2(或26228的5)所在列，千位=8(或6)，百位=0，十位=5(或2)，个位=4(或5)。

博主可能预测了26230期的"尾数"（个位候选数字）。
请：
1. 说明图中布局：是否标准5列走势表？是否有独立的"尾数"行/区域？rows_visible 列出可见期号行。
2. 执行列位对齐协议（若该图布局特殊如无标准开奖行，calibration_ok 填 false 并说明）。
3. 找出博主对26230期(或标注为下期)的个位/尾数预测：数字有哪些？写在图中哪个位置（个位列下方？独立尾数行？）？填入 position_digits 的"个"，或若在独立尾数区域则写进 annotations_text 并说明。
4. verdict 对照个位=3 说明命中情况。
按JSON返回：{{"filename":"...","rows_visible":"...","calibration_row":"26229","calibration_digits":[],"calibration_ok":true,"calibration_detail":"...","position_digits":{{"万":[],"千":[],"百":[],"十":[],"个":[]}},"kill_entries":[],"kill_positions":[],"annotations_text":"...","position_confidence":"...","verdict":"...","x_evidence":"..."}}"""


def build_special_prompt(path, kind, filename):
    if kind == "kill":
        return KILL_PROMPT.format(path=path, ALIGN=ALIGN)
    if kind == "tail":
        return TAIL_PROMPT.format(path=path, ALIGN=ALIGN)
    return build_prompt(filename)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="infile", required=True)
    ap.add_argument("--crops", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--extra-files", default="", help="逗号分隔的额外复测文件名")
    ap.add_argument("--model", default="deepseek-v4-flash-vision-exp", help="视觉模型，可切换 glm-5.3-flash")
    args = ap.parse_args()
    sys.stdout.reconfigure(errors="replace")

    import verify_chart_hits as vch
    vch.MODEL = args.model

    api_key = load_api_key()
    if not api_key:
        print("找不到 DEEPSEEK1_API_KEY")
        sys.exit(1)

    data = json.load(open(args.infile, encoding="utf-8"))
    per_image = data["per_image"]
    verdicts = data["verdicts"]

    targets = {}
    for v in verdicts:
        if v["verdict"] in ("AMBIGUOUS", "ERROR"):
            targets.setdefault(v["file"], []).append(v)
    for f in [x.strip() for x in args.extra_files.split(",") if x.strip()]:
        if f not in targets:
            targets.setdefault(f, []).extend([v for v in verdicts if v["file"] == f])
    files = sorted(targets.keys())
    if not files:
        print("无需清理")
        sys.exit(0)
    print(f"清理 {len(files)} 张: {[f[:30] for f in files]}")

    def one(f):
        crop_name, kind = SPECIAL.get(f, ("_b45.jpg", "standard"))
        crop_path = os.path.join(args.crops, f.replace(".jpg", crop_name))
        if not os.path.exists(crop_path):
            crop_path = os.path.join(args.crops, "batch45", f.replace(".jpg", "_b45.jpg"))
        if not os.path.exists(crop_path):
            return f, [{"error": "裁剪图不存在: " + crop_path}]
        prompt = build_special_prompt(crop_path, kind, f)
        out = []
        for rd in range(3):
            for attempt in range(8):
                try:
                    raw = call_vision(api_key, crop_path, prompt, timeout=600)
                    v = extract_json(raw)
                    if v is None:
                        time.sleep(2 + attempt)
                        if attempt == 7:
                            out.append({"error": f"第{rd+1}读失败"})
                        continue
                    out.append(v)
                    break
                except urllib.error.HTTPError as e:
                    time.sleep(10 * (attempt + 1))
                    if attempt == 7:
                        out.append({"error": f"第{rd+1}读HTTP{e.code}"})
                except Exception as e:
                    time.sleep(5 * (attempt + 1))
                    if attempt == 7:
                        out.append({"error": f"第{rd+1}读{e}"})
        return f, out

    new_reads = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        for f, reads in ex.map(one, files):
            new_reads[f] = reads
            kind = SPECIAL.get(f, ("", "standard"))[1]
            print(f"  {f[:44]}: {sum(1 for r in reads if 'error' not in r)}/3 读成功 kind={kind}")

    for v in verdicts:
        f = v["file"]
        if f not in targets:
            continue
        reads = [r for r in new_reads.get(f, []) if "error" not in r]
        if not reads:
            v["note"] += " || 清理仍失败"
            continue
        old_good = [r for r in per_image.get(f, {}).get("reads", []) if "error" not in r and r.get("calibration_ok")]
        all_reads = old_good + reads
        votes = [judge_record(v, r)[0] for r in all_reads]
        final = majority(votes)
        v["verdict"] = final
        v["note"] += f" || 对齐复核: calib={[r.get('calibration_digits') for r in reads]} 判定={votes} [{reads[-1].get('x_evidence','')[:100]}]"
        v["basis"] = "machine-对齐复核"
        per_image.setdefault(f, {}).setdefault("reads", []).extend(reads)

    summary = {}
    for v in verdicts:
        summary[v["verdict"]] = summary.get(v["verdict"], 0) + 1
    print("=== 最终汇总 ===", summary)
    data["summary"] = summary
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print("输出:", args.out)
    for v in verdicts:
        print(f"  [{v['verdict']:9s}] {v['blogger']} | {v['file'][:24]} | {v['type']} {v['position']} {v['numbers']} | {v['note'][:80]}")


if __name__ == "__main__":
    main()
