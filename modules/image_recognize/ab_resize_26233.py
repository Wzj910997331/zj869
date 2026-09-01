#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ab_resize_26233.py — A/B 实测:原图 vs H≤512 识别耗时/质量。

串行、每请求 timeout 240,后台运行避免与主识别任务抢带宽。
"""
import sys, os, io, base64, time, json
from PIL import Image
REPO = "/data/zhenjie/zj869"
sys.path.insert(0, os.path.join(REPO, "modules", "image_recognize"))
import analyze_crops_ds as ac

MAN = json.load(open(os.path.join(REPO, "data/recognize/20260831_all/crops_all_manifest.json")))["images"]
OUT_ROOT = os.path.join(REPO, "data/recognize/20260831_all")
OUT = "/tmp/claude-0/-data-zhenjie-ac-bench/8a82e787-ca6c-43f0-9cf6-b9e529b946da/tasks/btv3cp87f.output"

# 从当前任务已完成图里挑高/低 2 张
done = set()
for line in open(OUT, encoding="utf-8"):
    if "✓" in line and "_" in line:
        done.add(line.split("✓")[1].split()[0])
cands = []
for f in done:
    rec = MAN.get(f)
    if not rec or not rec.get("crop_dir"):
        continue
    p = os.path.join(OUT_ROOT, rec["crop_dir"], "02_annotated.png")
    if os.path.exists(p):
        im = Image.open(p)
        cands.append((f, im.size[0], im.size[1], p))
cands.sort(key=lambda c: c[2])
picks = [cands[-1], cands[0]] if len(cands) > 1 else cands
print(f"A/B 选图: {[(f, f'{w}x{h}') for f, w, h, _ in picks]}", flush=True)


def b64img(im):
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


PROMPT = ('这张图是博主在走势图上手画的预测标注(已裁剪为标注行栈,每行左侧红色 row 标签;'
          '数字区从左到右为万/千/百/十/个位)。博主预测目标期是26233期。请列出博主画的所有预测标注:'
          '每条含 type(定位/斜连/胆码/头/尾/和值/杀号)、position(万/千/百/十/个,无则null)、'
          'numbers(数字列表)、desc(一句话含标注方式与预测数字)。博主常画2-4个位置,务必全列。'
          '只回JSON:{"type":"","period":"26233","patterns":[{"type":"","position":null,"numbers":[],"desc":""}]}')


def run(uri, tag):
    msgs = [{"role": "user", "content": [{"type": "text", "text": PROMPT},
                                          {"type": "image_url", "image_url": {"url": uri}}]}]
    t0 = time.time()
    try:
        raw = ac.call_ds_vision(msgs, timeout=240, max_tokens=16000)
        return time.time() - t0, raw, "ok"
    except Exception as e:
        return time.time() - t0, "", f"ERR:{e}"


for f, w, h, p in picks:
    im = Image.open(p)
    dt1, raw1, st1 = run(b64img(im), "orig")
    print(f"[{f}] 原图 {w}x{h}: {dt1:.1f}s {st1}", flush=True)
    r = im.copy().resize((max(1, int(w * 512 / h)), 512), Image.LANCZOS)
    dt2, raw2, st2 = run(b64img(r), "r512")
    n1 = str(raw1).count('"type"')
    n2 = str(raw2).count('"type"')
    print(f"[{f}] H512 {r.size[0]}x512: {dt2:.1f}s {st2}  (type数 {n1}->{n2})", flush=True)
    print(f"  原图:{str(raw1)[:120]}", flush=True)
    print(f"  H512:{str(raw2)[:120]}", flush=True)
print("A/B 完成", flush=True)
