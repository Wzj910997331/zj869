#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""read_blogger_prediction.py — 第2道门：GLM-Vision 拼图批量读「目标期行」博主手写。

把窄条裁到 5 数字列区（去期号/空白）→ 每批 --batch 张拼成一张纵向拼接图 → 调 glm-5.3-flash
一次读多条（比逐张 GLM 快，且比 DS-Vision 读位更准——DS 读窄条系统性列位偏移：跳过期号/和值
列造成万千百十个右移 1 格，产生假命中）。GLM 布局/中文理解强，配合校准行锚定列位更稳。

输入：extract_prediction_strip 的 strips/manifest.json + *_strip.png。
输出：data/crawl/<date>/blogger_predictions.json（schema 同旧版，供 verify_blogger_prediction 复用）：
  {预测: [...]}，每条预测 = {位置,候选}；候选=该位置博主写的数字列表（1个=单码，≥2=二选/多码(C)）。
  和值/组选/跨度 → 整图 reject_reason；无数字 → 预测:[]（B 空读）。

用法:
  python3 modules/image_recognize/read_blogger_prediction.py --date 20260829 \
      --period 26231 --draw "1 8 7 9 9" --calib 26230 --calib-draw "9 4 6 8 3" \
      --strips data/crawl/20260829/strips --posts data/crawl/20260829/posts.json \
      --imp data/crawl/20260829/image_patterns_with_blogger.json \
      --out data/crawl/20260829/blogger_predictions.json --batch 8 --workers 3
"""
import argparse
import concurrent.futures
import io
import json
import os
import re
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_IMG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _IMG)  # modules/image_recognize，供 import analyze_crops_ds

import analyze_crops_ds as DS  # noqa: E402

COL_POS = ["万", "千", "百", "十", "个"]


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def blogger_map(posts_path, imp_path):
    m = {}
    if posts_path and os.path.exists(posts_path):
        for p in read_json(posts_path):
            c = p.get("creator") or {}
            nm = c.get("name") or p.get("user_mark") or ""
            if nm and p.get("id"):
                m[p["id"]] = nm
    if imp_path and os.path.exists(imp_path):
        for r in read_json(imp_path):
            if r.get("file") and r.get("blogger") and r["file"] not in m:
                m[r["file"]] = r["blogger"]
    return m


def resolve_blogger(file, maps):
    mm = re.match(r"(s_2_[0-9a-f-]+)_\d+\.(?:jpg|jpeg|png)$", file)
    if mm and mm.group(1) in maps:
        return maps[mm.group(1)]
    return maps.get(file, "")


def col_crop(img, file, meta, fr_entry, strip_type):
    """把窄条裁到 5 数字列区（去期号/空白），返回裁剪图。无法定位列 → 返回原图。

    strip_type=cols: meta.cols/x_range 是原图坐标 → (c-x0)*3 得条内坐标。
    strip_type=row : filter_report.cols 是原图坐标，条为全宽band → c*3。
    """
    W = img.shape[1]
    centers = None
    if strip_type == "cols" and meta.get("cols"):
        x0 = meta.get("x_range", [0])[0]
        centers = [(c - x0) * 3 for c in meta["cols"]]
    elif (fr_entry or {}).get("cols"):
        centers = [c * 3 for c in fr_entry["cols"]]
    if not centers or len(centers) < 5:
        return img
    pitch = np.median([centers[i + 1] - centers[i] for i in range(4)])
    half = max(6 * 3, int(pitch / 2))
    cx0 = max(0, int(centers[0] - half))
    cx1 = min(W, int(centers[-1] + half))
    if cx1 - cx0 < 60:
        return img
    return img[:, cx0:cx1]


def build_montage(tiles):
    """tiles: list[(label, PIL.Image|ndarray)] 纵向堆叠。每条统一缩放到宽 TARGET_W（压缩高度、
    复刻实测 4s 可读尺度）。返回 montage Image。"""
    TARGET_W = 760
    MAX_H = 180          # 每条缩到 760 宽后若过高再按高缩，保证 montage 紧凑、DS 好读
    header = 24
    ims = []
    for label, t in tiles:
        if isinstance(t, np.ndarray):
            im = Image.fromarray(t.astype("uint8"))
        else:
            im = t
        if im.width != TARGET_W:
            im = im.resize((TARGET_W, max(1, int(im.height * TARGET_W / im.width))))
        if im.height > MAX_H:
            im = im.resize((max(1, int(im.width * MAX_H / im.height)), MAX_H))
        canv = Image.new("RGB", (im.width, im.height + header), "white")
        canv.paste(im, (0, header))
        ImageDraw.Draw(canv).text((3, 3), label, fill="red")
        ims.append(canv)
    H = sum(i.height for i in ims)
    mont = Image.new("RGB", (TARGET_W, H), "white")
    y = 0
    for i in ims:
        mont.paste(i, (0, y))
        y += i.height
    return mont


def glm_prompt(period, calib=None):
    calib_txt = ""
    if calib:
        calib_txt = (f"\n对齐锚定：图中可能可见上一期开奖行，{calib[0]} = {calib[1]}"
                     "（万=第1个数字 千=第2 百=第3 十=第4 个=第5）。"
                     "若可见，用它校准\"哪个格是万位\"，确保位名准确。")
    return (f"图片是 N 张竖直堆叠的排列五走势图，每张是「{period} 期目标期行」的窄条。"
            "每张顶部标了红色编号 #NNN。\n"
            "\n"
            "每张窄条从左到右包含 5 个开奖数字格 = 万位、千位、百位、十位、个位（固定顺序），"
            "即第1格=万、第5格=个。若最左还混有期号/和值列（数字、文字），一律忽略，只认这 5 个开奖格。\n"
            "\n"
            "博主在部分格子里**手写/圈**了彩色数字（红/紫/蓝）。对每张，输出严格 JSON 数组，元素格式：\n"
            "[{{\"idx\":\"#NNN\",\"预测\":[{{\"位置\":\"百位\",\"候选\":[4]}},"
            "{{\"位置\":\"万位\",\"候选\":[3,5]}}]}}]\n"
            "\n"
            "规则：\n"
            "- 候选 = 该位置博主写的**所有数字**（1 个=单码；≥2 个=二选/多码）。\n"
            "- 位置必须用**位名**（万/千/百/十/个）报告；左→右固定是 万千百十个，第1格=万、第5格=个。\n"
            "- 博主没写的格子**不要**出现在列表（只收有数字的位）。\n"
            "- 若是和值/组选/跨度 → 位置写\"和值\"，候选给该值。\n"
            "- 若博主这行**什么都没写**（只有圈/线/空白）→ \"预测\":[]。\n"
            "- 数量=图片里贴的张数，不多不少，idx 严格等于标签。\n"
            f"{calib_txt}\n"
            "**不要思考、不要推理**：你只需直接把图上的彩色数字读出来、按上述格式填 JSON，"
            "不要犹豫、不要分析规律、不要加任何说明文字。只输出一个 JSON 数组，其余一律不写。")


# 位置别名归一化（"百"/"百位"/"第3位"→百）
ALIAS = {"万": "万位", "千": "千位", "百": "百位", "十": "十位", "个": "个位",
         "万位": "万位", "千位": "千位", "百位": "百位", "十位": "十位", "个位": "个位"}
REJECT_KW = ("和值", "组", "跨", "胆", "合")


def norm_pos(s):
    s = str(s).strip().replace("位", "").strip()
    # 取第一个位名
    for c in COL_POS:
        if c in s:
            return ALIAS[c]
    return s if s else None


def parse_ds_out(struct, file):
    """把 DS 返回的 {预测:[{位置,候选}]} 归一成 predicted_positions + reject_reason"""
    preds = []
    for p in (struct or {}).get("预测", []) or []:
        pos = norm_pos(p.get("位置", ""))
        cand = p.get("候选", [])
        if isinstance(cand, (str, int)):
            cand = [cand]
        nums = []
        for c in cand:
            try:
                nums.append(int(str(c).strip()))
            except (ValueError, TypeError):
                continue
        if not pos:
            continue
        if pos == "和值" or any(k in str(p.get("位置", "")) for k in REJECT_KW):
            return [], f"和值/组选/胆(非定位单码)：{p.get('位置')}{''.join(map(str, nums))}"
        if not nums:
            continue
        preds.append({"位置": pos, "候选": nums, "标注方式": "手写/圈", "原文": ""})
    return preds, ""


def extract_json_array(text):
    """从模型文本抽首个平衡 JSON 数组并解析。GLM 偶发带 ```json 围栏 / 前后散文 / 对象包裹，
    裸 json.loads 会因非数组报错 → 用此健壮抽取。返回 list 或 None。"""
    if not text:
        return None
    s = text
    # 剥 markdown 围栏
    if "```" in s:
        m = re.search(r"```(?:json)?\s*(.*?)```", s, re.S)
        if m:
            s = m.group(1)
    # 先直接试（含剥围栏后的整体）
    try:
        v = json.loads(s)
        if isinstance(v, list):
            return v
    except json.JSONDecodeError:
        pass
    # 找首个 '[' 平衡']' 抽出数组体
    start = s.find("[")
    if start != -1:
        depth = 0
        for i in range(start, len(s)):
            ch = s[i]
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[start:i + 1])
                    except json.JSONDecodeError:
                        return None
    return None


def one_batch(batch, api_ctx):
    """batch: list of (label, file, meta, strip_type, blogger)。返回 {file: record}。"""
    # 裁列并拼图
    fr = api_ctx["fr"]
    tiles = []
    # 用整条 raw（1:1 缩到宽 TARGET_W）+ build_montage 高度上限，复刻实测 4s 的紧凑尺度。
    # 不 col_crop：裁窄只缩宽不缩高，再 resize 到 760 反而把高放大(250-390px) → montage 过高，
    # DS patch 太多，后端负载下易超时。raw 整条缩到 760 宽后高仅 ~90-125px，稳定可读。
    for label, file, meta, strip_type, blogger in batch:
        stem = os.path.splitext(file)[0]
        p = os.path.join(api_ctx["strips_dir"], f"{stem}_strip.png")
        if not os.path.exists(p):
            continue
        img = np.array(Image.open(p).convert("RGB"))
        tiles.append((label, img))
    if not tiles:
        return {b[1]: {"file": b[1], "error": "缺图"} for b in batch}
    mont = build_montage(tiles)
    buf = io.BytesIO()
    mont.save(buf, format="PNG")
    b64 = __import__("base64").b64encode(buf.getvalue()).decode()
    n = len(tiles)
    prompt = glm_prompt(api_ctx["period"], api_ctx.get("calib")).replace("N 张", f"{n} 张")
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]}]
    model = api_ctx.get("model", DS.GLM_MODEL)
    if api_ctx.get("auto"):
        # 2026-09-02：网关有界自动切换。单次调用超时/断连/空返 → glm→ds 换家，绝不 4 次重试拖批
        raw, used = DS.call_vision_auto(msgs, providers=("glm", "ds"),
                                        max_tokens=16000, timeout=max(240, 45 * n))
        model = f"auto({used})" if raw else "auto(glm→ds均失败)"
    else:
        raw = DS.call_llm(model, msgs, max_tokens=16000, timeout=max(240, 45 * n))
    files = [b[1] for b in batch]
    out = {f: {"file": f, "blogger": b[4]} for f, b in zip(files, batch)}
    if not raw:
        for f in files:
            out[f]["error"] = f"{model} 空返回"
        return out
    arr = extract_json_array(raw)
    if not isinstance(arr, list):
        for f in files:
            out[f]["error"] = f"{model} 输出非 JSON 数组"
        return out
    # 按标签匹配：兼容「元素为 dict(带 idx)」与「元素为预测数组(DS 不带 idx，按 montage 上下顺序对齐)」
    labels_seq = [l.lstrip("#") for l, _, _, _, _ in batch]
    got = {}
    if all(isinstance(x, dict) for x in arr):
        for item in arr:
            lab = str(item.get("idx", "")).strip().lstrip("#")
            got[lab] = item
    else:
        for lab, item in zip(labels_seq, arr):
            if isinstance(item, dict):
                got[lab] = item
            elif isinstance(item, list):
                pred_lst = [p for p in item if isinstance(p, dict)]
                got[lab] = {"idx": lab, "预测": pred_lst}
    for label, file, meta, strip_type, blogger in batch:
        lab = label.lstrip("#")
        item = got.get(lab)
        rec = out[file]
        if item is None:
            rec["error"] = f"{model} 未返回该条"
            continue
        preds, rej = parse_ds_out(item, file)
        rec["predicted_positions"] = preds
        rec["target_period"] = api_ctx["period"]
        rec["strip_type"] = strip_type
        if rej:
            rec["reject_reason"] = rej
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--period", required=True)
    ap.add_argument("--draw", required=True)
    ap.add_argument("--calib", default="")
    ap.add_argument("--calib-draw", default="")
    ap.add_argument("--strips", required=True)
    ap.add_argument("--posts", default="")
    ap.add_argument("--imp", default="")
    ap.add_argument("--out", default=None)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--model", default="glm",
                    help="视觉模型：glm(默认 glm-5.3-flash，位准) / ds(deepseek-v4-flash-vision) / "
                         "auto(自动切换 glm→ds：每家单次有界，超时/断连/空返换家)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--cutoff", default="21:30",
                    help="复盘边界：'21:30'(同日只比时分) 或 '2026-08-31 21:30'(跨天目录比完整时间)")
    args = ap.parse_args()

    man = read_json(os.path.join(args.strips, "manifest.json"))
    all_items = sorted(((f, v) for f, v in man["images"].items() if v.get("ok")),
                       key=lambda t: t[0])

    # 开奖前发帖过滤
    posts_time = {}
    if args.cutoff and args.posts and os.path.exists(args.posts):
        for p in read_json(args.posts):
            if p.get("id") and p.get("create_time"):
                posts_time[p["id"]] = p["create_time"]

    def posttime(file):
        mm = re.match(r"(s_2_[0-9a-f-]+)_\d+\.(?:jpg|jpeg|png)$", file)
        return posts_time[mm.group(1)] if (mm and mm.group(1) in posts_time) else ""

    recap = []
    if args.cutoff:
        # 复盘边界：--cutoff 可给完整时间 "2026-08-31 21:30"(跨天目录必需，否则 09-01 复盘帖
        # 的 00:xx<21:30 会漏剔) 或仅 "21:30"(同日目录，向后兼容只比时分)。
        full_cut = "-" in args.cutoff
        keep, rec = [], []
        for f, v in all_items:
            t = posttime(f)
            is_recap = bool(t) and (t >= args.cutoff if full_cut else t[11:16] >= args.cutoff)
            if is_recap:
                v["reason"] = f"开奖后发帖(复盘/下期，{t[:16]})；不读"
                rec.append((f, v))
            else:
                keep.append((f, v))
        all_items = keep
        recap = rec
        print(f"开奖前发帖读: {len(all_items)}；复盘跳过: {len(recap)}")

    if args.limit:
        all_items = all_items[:args.limit]
        print(f"  --limit {args.limit}")
    if not all_items:
        print("无可读窄条")
        return

    maps = blogger_map(args.posts, args.imp if args.imp else os.path.join(
        os.path.dirname(os.path.dirname(args.strips)), "image_patterns_with_blogger.json"))
    fr = read_json(os.path.join(os.path.dirname(args.strips),
                                "filter_report.json"))["images"]

    # 切成批
    items = [(f, v, resolve_blogger(f, maps)) for f, v in all_items]
    batches = []
    for i in range(0, len(items), args.batch):
        chunk = items[i:i + args.batch]
        labels = [(f"{i + j:03d}", f, v, v.get("strip_type", "row"), b)
                  for j, (f, v, b) in enumerate(chunk)]
        batches.append(labels)

    existing = {}
    if args.resume and args.out and os.path.exists(args.out):
        for p in read_json(args.out).get("predictions", []):
            existing[os.path.splitext(p["file"])[0]] = p

    m = args.model.lower()
    if m.startswith("a"):
        model = "auto(glm→ds)"       # 展示用；实际每批在 one_batch 里换家并回填 auto(<家>)
        api_ctx = {"strips_dir": args.strips, "fr": fr, "period": args.period,
                   "model": DS.GLM_MODEL, "auto": True}
        print("--model auto：视觉自动切换 glm→ds（每家单次有界，超时/断连/空返换家，不重试）")
    else:
        model = DS.GLM_MODEL if m.startswith("g") else DS.DS_MODEL
        api_ctx = {"strips_dir": args.strips, "fr": fr, "period": args.period, "model": model}
    if args.calib and args.calib_draw:
        api_ctx["calib"] = (args.calib, args.calib_draw)
    results = {}
    # resume：整批已读且全部无错 → 跳过；否则批只加一次
    todo = []
    for labels in batches:
        need = False
        for label, f, v, st, b in labels:
            stem = os.path.splitext(f)[0]
            if stem in existing and not existing[stem].get("error"):
                results[stem] = existing[stem]
            else:
                need = True
        if need:
            todo.append(labels)

    print(f"待读窄条 {len(items)} → {len(batches)} 批（每批 {args.batch} 张，workers {args.workers}）")
    todo = [b for b in todo if b]

    def task(labels):
        """单批读图：任何异常转成该批每条的 error 记录，绝不拖垮整个任务。"""
        try:
            return one_batch(labels, api_ctx)
        except Exception as e:
            print(f"  [batch异常] {e}")
            return {f: {"file": f, "error": f"batch异常: {e}"}
                    for lab, f, v, st, b in labels}

    n_done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        for out_map in ex.map(task, todo):
            for file, rec in out_map.items():
                results[os.path.splitext(file)[0]] = rec
            n_done += 1
            print(f"  批完成 {n_done}/{len(todo)}")

    preds = [results[k] for k in sorted(results)]
    skipped = [{"file": f, "gate": "skip", "reason": v.get("reason")} for f, v in recap]
    out = {"说明": f"{model} 拼图批量读博主手写 {args.period}（{args.batch}张/批，校准锚定 {args.calib}={args.calib_draw}）",
           "actual": [int(x) for x in args.draw.split()],
           "target_period": args.period,
           "calib": f"{args.calib}={args.calib_draw}",
           "n_strips": len(preds), "n_recap": len(recap),
           "predictions": preds, "skipped": skipped}
    if args.out:
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        n_ok = sum(1 for p in preds if not p.get("error"))
        n_rej = sum(1 for p in preds if p.get("reject_reason") and not p.get("error"))
        n_pred = sum(1 for p in preds if p.get("predicted_positions"))
        print("=" * 40)
        print(f"读 {n_ok}/{len(preds)} 成功（{len(preds)-n_ok} 失败），{n_rej} 和值/组选剔除，{n_pred} 有预测位置")
        print(f"  → {args.out}")


if __name__ == "__main__":
    main()
