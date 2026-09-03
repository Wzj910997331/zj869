# -*- coding: utf-8 -*-
"""审计 _6：从原图裁目标行宽 band(上下留白覆盖博主跨行笔迹)，开放式清点彩色笔迹。"""
import os, sys, io, base64
import numpy as np
from PIL import Image
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "modules", "image_recognize"))
import analyze_crops_ds as DS

SRC = "data/crawl/20260831/images/s_2_37476714-28f0-4139-a2ba-cbbd32f48a2a_6.jpeg"
im = Image.open(SRC).convert("RGB")
W, H = im.size
# 列中心(原图 x)
cols = {"万": 157, "千": 315, "百": 473, "十": 631, "个": 789}
# 目标行 y≈1842, 上下各放宽, 覆盖 y1692 开始的长笔迹
Y0, Y1 = 1660, 1970
crop = im.crop((0, Y0, W, Y1)).resize((1536, int((Y1 - Y0) * 1536 / W)), Image.LANCZOS)
print("crop", crop.size)
sc = 1536 / W
scale_cols = {k: int(v * sc) for k, v in cols.items()}

buf = io.BytesIO(); crop.save(buf, format="PNG")
b64 = base64.b64encode(buf.getvalue()).decode()
coltxt = " ".join(f"{k}≈x{s}" for k, s in scale_cols.items())
prompt = (
    "这是一张排列五走势图截图的一个横向长条区域（包含 2 行左右）。图中走势表格有竖直线分格，"
    f"该区域内 5 个开奖数字格(万/千/百/十/个)的水平中心大约在 {coltxt}（横坐标按本图宽度）。\n"
    "博主用**彩色笔**（红/紫/蓝/绿等）在图上画了标记。请**从左到右逐处清点本区域内所有彩色笔迹**，"
    "每个笔迹报告：\n"
    "1) 类型：是「圈/框选住一个数字」、「写了一个手写数字」、「连线/直线」、「斜线」还是「涂抹/下划线」；\n"
    "2) 若是圈或手写数字：读出里面的**数字值**；\n"
    "3) 所在大致横坐标 x（数字即可）；\n"
    "4) 所在纵位置（上/中/下）。\n"
    "注意：黑色打印字不是博主笔迹；浅色行背景也不是。只要博主额外用彩笔画的。\n"
    "输出 JSON 数组，每元素 {{type, value|null, x, ypos}}。按 x 从左到右排。只输出 JSON。"
)
msgs = [{"role": "user", "content": [
    {"type": "text", "text": prompt},
    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]}]
raw, used = DS.call_vision_auto(msgs, providers=("glm", "ds"), max_tokens=16000, timeout=240)
print("== used:", used)
print("== raw:", (raw[:2500] if raw else None))
if raw:
    open("/tmp/audit_26233_postb_band_raw.txt", "w").write(raw)
