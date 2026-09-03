# -*- coding: utf-8 -*-
"""审计 26233 生活很无奈 帖b(_6.jpeg)：逐列受控读目标行，定博主到底画了几位。
消除列位偏移歧义：把 strip 5 个开奖列各自单独裁块、顶部红标位名(万/千/百/十/个)，
让视觉模型按标签逐块读"博主彩色手写数字"，与打印黑字区分。
"""
import os, sys, io, json, base64
import numpy as np
from PIL import Image, ImageDraw
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "modules", "image_recognize"))
import analyze_crops_ds as DS

STRIP = "data/crawl/20260831/strips/s_2_37476714-28f0-4139-a2ba-cbbd32f48a2a_6_strip.png"
COLS = [157, 315, 473, 631, 789]   # 原图坐标, 万千百十个
POS = ["万位", "千位", "百位", "十位", "个位"]
X0 = 72

im = Image.open(STRIP).convert("RGB")
W, H = im.size
print(f"strip {W}x{H}")

tiles = []
crops = []
for j, (c, pn) in enumerate(zip(COLS, POS)):
    cx = (c - X0) * 3           # strip 坐标(3x)
    half = int(158 * 3 * 0.55)  # ±261 strip px ≈ ±87 orig, 不越邻列
    xa = max(0, cx - half); xb = min(W, cx + half)
    crop = im.crop((xa, 0, xb, H))
    crops.append((pn, np.array(crop)))
    # 顶标红字标签
    canv = Image.new("RGB", (crop.width, crop.height + 26), "white")
    canv.paste(crop, (0, 26))
    ImageDraw.Draw(canv).text((4, 4), f"#{j+1} {pn}", fill="red")
    tiles.append(canv)

mont = Image.new("RGB", (tiles[0].width, sum(t.height for t in tiles)), "white")
y = 0
for t in tiles:
    mont.paste(t, (0, y)); y += t.height
print(f"montage {mont.width}x{mont.height}")
buf = io.BytesIO()
mont.save(buf, format="PNG")
b64 = base64.b64encode(buf.getvalue()).decode()

prompt = (
    "图片是竖直堆叠的 5 个裁剪块，每个块顶部用红色文字标了编号 #1..#5 和位名"
    "（#1万位 #2千位 #3百位 #4十位 #5个位）。每个块显示排列五走势图**同一行**里对应列的窄带。\n"
    "该行里可能有**黑色**打印开奖数字；博主手写预测数字是**彩色/带圈/笔迹粗**的"
    "（红/紫/蓝），叠在格子里。\n"
    "对**每一块**：#k 下方的窄带里，博主有没有画**彩色手写数字或圈**？\n"
    "- 有：读那个彩色手写数字（单个 0-9；若是圈住的数就读圈里的数字）。\n"
    "- 没有彩色手写/只有黑色打印字或背景：写 EMPTY。\n"
    "输出严格 JSON 对象，键=标签号(1..5)，值=数字或 \"EMPTY\"。不要思考，直接读。只输出 JSON。"
)
msgs = [{"role": "user", "content": [
    {"type": "text", "text": prompt},
    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]}]

raw, used = DS.call_vision_auto(msgs, providers=("glm", "ds"), max_tokens=16000, timeout=240)
print("== used:", used)
print("== raw:", raw[:1200] if raw else None)
if raw:
    open("/tmp/audit_26233_postb_colread_raw.txt", "w").write(raw)
