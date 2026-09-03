# -*- coding: utf-8 -*-
"""审计 26233 情有独钟0(_2.jpg)：逐列受控读目标行，定 0/1 落在哪列(十位 or 个位？百 or 千?)。"""
import os, sys, io, base64
import numpy as np
from PIL import Image, ImageDraw
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "modules", "image_recognize"))
import analyze_crops_ds as DS

SRC = "data/crawl/20260831/images/s_2_b37e58f6-effe-4a02-a792-1d57d6880e9d_2.jpg"
COLS = [205, 355, 536, 680, 827]
POS = ["万位", "千位", "百位", "十位", "个位"]
Y0, Y1 = 1650, 1895     # 含 gate 高圈 1667..1879 全幅, 不裁顶
im = Image.open(SRC).convert("RGB")
W, H = im.size
print("orig", W, H)

tiles = []
for j, (c, pn) in enumerate(zip(COLS, POS)):
    half = 95   # orig px, 相邻列中心距 ~150-180
    xa = max(0, c - half); xb = min(W, c + half)
    crop = im.crop((xa, Y0, xb, Y1))
    s = 3
    crop = crop.resize((crop.width * s, crop.height * s), Image.LANCZOS)
    canv = Image.new("RGB", (crop.width, crop.height + 26), "white")
    canv.paste(crop, (0, 26))
    ImageDraw.Draw(canv).text((4, 4), f"#{j+1} {pn}", fill="red")
    tiles.append(canv)

mont = Image.new("RGB", (tiles[0].width, sum(t.height for t in tiles)), "white")
y = 0
for t in tiles:
    mont.paste(t, (0, y)); y += t.height
print("montage", mont.size)
buf = io.BytesIO(); mont.save(buf, format="PNG")
b64 = base64.b64encode(buf.getvalue()).decode()

prompt = (
    "图片是竖直堆叠的 5 个裁剪块，每块顶部红字标 #1万位 #2千位 #3百位 #4十位 #5个位。"
    "每块是排列五走势图**目标期(最下一行附近)**对应列的窄带，可能带一段上一行。\n"
    "黑色小字是打印开奖数字；博主用**彩色笔**(红/紫/蓝)画了线/圈/手写数字。\n"
    "逐块判断：#k 带内博主有没有画**彩色圈/框选 或 彩色手写数字**？\n"
    "- 有圈/框：读被圈住或框住的那个数字（0-9，黑字也可能被圈）。\n"
    "- 有彩色手写数字（没圈，笔迹彩色的）：读它。\n"
    "- 只有线经过、没圈没字：写 LINE。\n"
    "- 什么都没有：写 EMPTY。\n"
    "输出 JSON 对象 {标签号: 值或EMPTY/LINE}，只输出 JSON。不要推理。"
)
msgs = [{"role": "user", "content": [
    {"type": "text", "text": prompt},
    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]}]
raw, used = DS.call_vision_auto(msgs, providers=("glm", "ds"), max_tokens=16000, timeout=240)
print("== used:", used)
print("== raw:", (raw[:1200] if raw else None))
if raw:
    open("/tmp/audit_26233_qydz0_colread_raw.txt", "w").write(raw)
