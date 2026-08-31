# -*- coding: utf-8 -*-
"""全量命中图复核 v2：裁剪预测行 → 视觉模型双读(带真实开奖校准) → 对账旧记录 → 判定。

用法:
  python tools/verify_chart_hits.py \
      --records data/crawl/20260828/image_patterns_with_blogger.json \
      --images "C:\\Users\\zhenjie.wu\\.dsh\\work\\gouli_jpg" \
      --crops  "C:\\Users\\zhenjie.wu\\.dsh\\work\\gouli_crop\\batch45" \
      --out    data/crawl/20260828/verify_results.json \
      --workers 10

判定规则（每图两次独立读取，只读两次一致才定案）:
  CONFIRM   两次都确认旧记录数字在该列 → 命中成立
  REJECT    两次都判定该列无此数字/无预测 → 旧记录误判
  AMBIGUOUS 两次不一致 / 校准失败 → 待人工或放大复核
  KILL      杀号标记事实确认（不计入定位命中）
  NOPOS     无位次记录（杀号无位置等）

校准: 26228=5,6,0,2,5 / 26229=2,8,0,5,4（第1~5列=万-千-百-十-个），目标期 26230=9,4,6,8,3
密钥: 环境变量 DEEPSEEK1_API_KEY 或 $DSH_HOME/.credentials.yaml（不写入输出）
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
DRAW_26230 = [9, 4, 6, 8, 3]  # 万 千 百 十 个
POS_MAP = {"万位": 1, "千位": 2, "百位": 3, "十位": 4, "个位": 5}
POS_NAME = {1: "万", 2: "千", 3: "百", 4: "十", 5: "个"}


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


def load_crop_module():
    spec = importlib.util.spec_from_file_location("crop_charts", os.path.join(SCRIPT_DIR, "crop_charts.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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


def call_vision(api_key, image_path, prompt, timeout=600, model=None):
    if model is None:
        model = MODEL
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}},
                ],
            }
        ],
        "temperature": 0.1,
        "max_tokens": 4000,
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
    return data["choices"][0]["message"]["content"]


def build_prompt(filename, extra=""):
    return f"""你是图片分析专家。请用 read_image 工具读取图片：{filename}
这是"排列五走势图"底部区域（博主画规律/预测）。真实开奖（用于校准列位）：
- 26228期 = 5 6 0 2 5（万 千 百 十 个）
- 26229期 = 2 8 0 5 4（万 千 百 十 个）
- 26230期 = 9 4 6 8 3（万 千 百 十 个）—— 博主预测的目标期

【列位对齐协议——必须严格执行】
1. 在图中找到最近一期已开奖行 26229（若该行不在图中，改用 26228 行）。
2. 从左到右读出该行的【开奖数字】。注意：期号列、日期列、以及期号旁单独一列的辅助数值（如13/15，不是开奖数字）都要跳过。
3. 你读到的开奖数字必须依次等于真实开奖：26229 = 2,8,0,5,4（26228 = 5,6,0,2,5）。只有完全一致才说明对齐成功。
4. 若读出的数字不是 2,8,0,5,4（顺序或内容不符），说明列没对齐（辅助列被当成开奖列、或漏读某列）——请重新定位，直到读出的5个开奖数字与真实开奖完全一致。
5. calibration_row: 实际用于校准的行（"26229"或"26228"）。
6. calibration_digits: 最终读到的对齐行5个数字，如["2","8","0","5","4"]。
7. calibration_ok: 仅当 calibration_digits 与真实开奖完全一致时填 true，否则 false 并在 calibration_detail 说明。

【位名锚定——关键】以校准行的5个开奖数字锚定5个位置：
  万位 = 校准行数字2（或26228行的5）所在的列
  千位 = 数字8（或6）所在的列
  百位 = 数字0所在的列
  十位 = 数字5（或2）所在的列
  个位 = 数字4（或5）所在的列
之后报告预测一律用位名（万/千/百/十/个），不用"第几列"。

对齐+锚定完成后，再读 26230 预测行：
8. rows_visible: 列出图中可见的期号行。
9. position_digits: 26230行出现的所有预测数字按位名分组，如{{"万":["9"],"千":[],"百":["6"],"十":[],"个":["3"]}}。组合候选如"4/9"记["4","9"]；某位无预测记[]。圈选/手写的数字都算；居中无位次的手写文字不要放入，改放annotations_text。
10. kill_entries: 26230行（或邻近）打X/划线杀号标记逐条列出，如"千位: 杀0、5"；kill_positions: 被杀位名数组，如["千"]；无则空数组。
11. annotations_text: 无位次居中手写文字（如"4++3 防9++8"）、未来期异常数字等。
12. position_confidence: 位名判断置信度（高/中/低）及理由。
13. verdict: 对照26230开奖 9,4,6,8,3 逐位说明position_digits中哪些命中。
{extra}
重要：26231及以后为未来期，图中应为空白；若读到数字，属可疑/博主预填，只在annotations_text注明，不要纳入position_digits，不得用于校准。
请只报告图中真实可见内容，不猜测。严格按以下JSON返回，不要输出JSON以外的任何文字：
{{"filename":"...","rows_visible":"...","calibration_row":"26229","calibration_digits":[],"calibration_ok":true,"calibration_detail":"...","position_digits":{{"万":[],"千":[],"百":[],"十":[],"个":[]}},"kill_entries":[],"kill_positions":[],"annotations_text":"...","position_confidence":"...","verdict":"..."}}"""


def pos_to_col(p):
    if not p:
        return None
    p = str(p).strip()
    if p in POS_MAP:
        return POS_MAP[p]
    m = re.match(r"^第([1-5])(?:位|列|个格子|格)?$", p)
    if m:
        return int(m.group(1))
    return None


def pos_to_name(p):
    """位置 → 位名(万/千/百/十/个)；失败返回None"""
    col = pos_to_col(p)
    if col is None:
        return None
    return POS_NAME[col]


def get_pred_digits(v, name):
    """从视觉结果取某位名(万/千/百/十/个)的预测数字。优先 position_digits，回退 column_digits。"""
    pd = v.get("position_digits") or {}
    if pd:
        return [str(x) for x in pd.get(name, [])]
    cd = v.get("column_digits") or {}
    col = {k: i + 1 for i, k in enumerate(["万", "千", "百", "十", "个"])}.get(name)
    return [str(x) for x in cd.get(str(col), [])] if col else []


def judge_record(record, v):
    """对单条旧记录 vs 单次视觉结果判定。返回 (verdict, note)。"""
    rtype = record.get("type", "")
    nums = [int(x) for x in (record.get("numbers") or [])]
    name = pos_to_name(record.get("position"))

    if rtype == "杀号":
        if name is None:
            return "NOPOS", "无位次杀号记录，不参与定位判定"
        kp = v.get("kill_positions") or []
        if name in kp or pos_to_col(name) in kp:
            return "KILL", f"视觉确认{name}位有打X杀号标记"
        return "AMBIGUOUS", f"视觉未见{name}位杀号标记(或标记不明显)"

    if name is None:
        return "NOPOS", f"无位次记录(type={rtype})，不参与定位判定"

    pred_digits = get_pred_digits(v, name)
    nums_s = [str(x) for x in nums]

    if not v.get("calibration_ok"):
        return "AMBIGUOUS", "无有效校准行，位名不可靠"

    # 校准数字核验：即使 calibration_ok=true，calibration_digits 也必须与真实开奖一致
    cd = v.get("calibration_digits") or []
    cd_s = [str(x) for x in cd]
    row = v.get("calibration_row") or "26229"
    exp_map = {"26229": ["2", "8", "0", "5", "4"], "26228": ["5", "6", "0", "2", "5"]}
    exp = exp_map.get(str(row), ["2", "8", "0", "5", "4"])
    if cd_s and cd_s != exp:
        return "AMBIGUOUS", f"校准数字{cd_s}与{row}真实开奖{exp}不符，位名不可靠"

    if not pred_digits:
        return "REJECT", f"视觉读取{name}位无预测数字（旧记录声称有）"

    hit = set(nums_s) & set(pred_digits)
    if hit:
        return "CONFIRM", f"{name}位预测数字{pred_digits}包含旧记录数字{hit}"
    return "REJECT", f"{name}位实际预测数字为{pred_digits}，与旧记录{nums_s}不符"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--records", required=True)
    ap.add_argument("--images", default=r"C:\Users\zhenjie.wu\.dsh\work\gouli_jpg")
    ap.add_argument("--crops", default=r"C:\Users\zhenjie.wu\.dsh\work\gouli_crop\batch45")
    ap.add_argument("--out", default="data/crawl/20260828/verify_results.json")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--skip-crop", action="store_true", help="裁剪图已存在时跳过")
    ap.add_argument("--settled", default=None,
                    help="已人工复核的判定JSON(与records同结构+verdict/note字段)，这些图跳过机器读取直接合并")
    ap.add_argument("--model", default="deepseek-v4-flash-vision-exp",
                    help="视觉模型，可切换 glm-5.3-flash 等")
    args = ap.parse_args()
    sys.stdout.reconfigure(errors="replace")
    global MODEL
    MODEL = args.model

    api_key = load_api_key()
    if not api_key:
        print("找不到 DEEPSEEK1_API_KEY（环境变量或 $DSH_HOME/.credentials.yaml）")
        sys.exit(1)

    records = json.load(open(args.records, encoding="utf-8"))
    hits = [r for r in records if r.get("hit")]
    settled = []
    if args.settled and os.path.exists(args.settled):
        settled = json.load(open(args.settled, encoding="utf-8"))
    settled_by_file = {}
    for s in settled:
        settled_by_file.setdefault(s["file"], []).append(s)
    settled_files = set(settled_by_file.keys())

    to_verify = [r for r in hits if r["file"] not in settled_files]
    by_file = {}
    for r in to_verify:
        by_file.setdefault(r["file"], []).append(r)
    files = sorted(by_file.keys())
    print(f"hit 记录 {len(hits)} 条 | 人工已定 {len(settled)} 条 | 机器复核 {len(to_verify)} 条 / {len(files)} 图")

    # 1) 裁剪（原分辨率 bottom45，高<=1300 用全图）
    crop_mod = load_crop_module()
    os.makedirs(args.crops, exist_ok=True)
    crop_paths = {}
    for f in files:
        src = os.path.join(args.images, f)
        if not os.path.exists(src):
            print("MISSING ORIG:", f)
            continue
        img = crop_mod.Image.open(src).convert("RGB")
        w, h = img.size
        if h <= 1300:
            out_name, out_img = crop_mod.crop_full(img, 99999, "_full")
        else:
            out_name, out_img = crop_mod.crop_bottom45(img, 99999, "_b45")
        out_path = os.path.join(args.crops, f.replace(".jpg", out_name + ".jpg"))
        if not args.skip_crop or not os.path.exists(out_path):
            out_img.save(out_path, "JPEG", quality=90)
        crop_paths[f] = out_path
    print(f"裁剪完成: {len(crop_paths)} 张 -> {args.crops}")

    # 2) 视觉双读（并行）
    EXTRA2 = ("特别提醒：数字若画在列边界附近容易读错列。请以26229校准行的列位置为参照，"
              "仔细核对26230行每个数字的列位；若某数字位于两列边界附近，说明其更接近哪列及理由，"
              "column_digits 中放你最有把握的列，并在 annotations_text 注明可疑。")

    def one(f):
        reads = []
        for i, extra in enumerate(["", EXTRA2]):
            for attempt in range(6):
                try:
                    raw = call_vision(api_key, crop_paths[f], build_prompt(f, extra), timeout=600)
                    v = extract_json(raw)
                    if v is None:
                        # 空响应：网关间歇性问题，快速重试
                        time.sleep(2 + attempt)
                        if attempt == 5:
                            reads.append({"error": f"第{i+1}读失败: 6次空响应"})
                        continue
                    reads.append(v)
                    break
                except urllib.error.HTTPError as e:
                    time.sleep(10 * (attempt + 1))
                    if attempt == 5:
                        reads.append({"error": f"第{i+1}读失败: HTTP {e.code}"})
                except Exception as e:
                    time.sleep(5 * (attempt + 1))
                    if attempt == 5:
                        reads.append({"error": f"第{i+1}读失败: {e}"})
        return f, reads

    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        for f, reads in ex.map(one, files):
            results[f] = {"reads": reads, "records": by_file[f]}
            ok = [r.get("calibration_ok") for r in reads if "error" not in r]
            print(f"  done {f}: calib={ok} col1={[r.get('column_digits',{}).get('1') for r in reads if 'error' not in r]}")

    # 3) 双读对账判定 + 合并人工已定记录
    verdicts = []
    for f, item in results.items():
        reads = [r for r in item["reads"] if "error" not in r]
        for r in item["records"]:
            if len(reads) < 2:
                verdict, note = "ERROR", "视觉读取失败"
            else:
                v1, n1 = judge_record(r, reads[0])
                v2, n2 = judge_record(r, reads[1])
                if v1 == v2 and v1 in ("CONFIRM", "REJECT", "KILL", "NOPOS"):
                    verdict, note = v1, n1 + " || " + n2
                else:
                    verdict, note = "AMBIGUOUS", f"两读不一致: [{v1}] {n1}  vs  [{v2}] {n2}"
            # 校验旧命中不变式: draw[col] 应在旧记录数字内
            col = pos_to_col(r.get("position"))
            inv = ""
            if col is not None and r.get("numbers"):
                dv = DRAW_26230[col - 1]
                if dv not in [int(x) for x in r["numbers"]]:
                    inv = f" [警告: 旧记录数字不含开奖{dv}@{col}列]"
            verdicts.append({
                "blogger": r.get("blogger"), "file": f, "type": r.get("type"),
                "position": r.get("position"), "numbers": r.get("numbers"),
                "desc": r.get("desc"), "verdict": verdict, "note": note + inv,
                "basis": "machine-双读",
            })
    for s in settled:
        verdicts.append({
            "blogger": s.get("blogger"), "file": s.get("file"), "type": s.get("type"),
            "position": s.get("position"), "numbers": s.get("numbers"),
            "desc": s.get("desc"), "verdict": s.get("verdict"), "note": s.get("note", ""),
            "basis": "manual-人工复核",
        })

    summary = {}
    for vt in set(x["verdict"] for x in verdicts):
        summary[vt] = sum(1 for x in verdicts if x["verdict"] == vt)
    print("=== 判定汇总 ===", summary)

    out = {
        "meta": {"draw_26230": DRAW_26230, "workers": args.workers, "model": MODEL,
                 "total_records": len(verdicts), "rule": "双读一致才定案"},
        "summary": summary,
        "per_image": results,
        "verdicts": verdicts,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("输出:", args.out)
    print("=== 逐条判定 ===")
    for x in verdicts:
        print(f"  [{x['verdict']:9s}] {x['blogger']} | {x['file'][:22]} | {x['type']} {x['position']} {x['numbers']} | {x['note'][:90]}")


if __name__ == "__main__":
    main()
