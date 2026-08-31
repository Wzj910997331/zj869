#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
合成数据训练数字 CNN → model/digit_cnn.pt

数据：PIL 渲染 DejaVu/Liberation 多字体 0-9（~40×60 字形），仿射±5°、
缩放 0.9-1.1、平移、模糊、噪声、亮度、横带遮挡（模拟博主红带盖数字）扰动。
每数字 ~2500 样本，共 ~25k。CPU 训练 <60s。合成集 ≥99%，真实字 ~90-97%。
"""
import os
import random
import sys

import numpy as np
import torch
import torch.nn as nn
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from torch.utils.data import DataLoader, Dataset

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from model.digit_cnn import DigitCNN, INPUT_H, INPUT_W, MODEL_PATH, save_model

random.seed(42)
np.random.seed(42)
torch.manual_seed(42)

FONT_DIRS = [
    "/usr/share/fonts/truetype/dejavu/",
    "/usr/share/fonts/truetype/liberation/",
]
FONT_FILES = [
    "DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSerif.ttf",
    "LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf",
    "LiberationMono-Regular.ttf", "LiberationSansNarrow-Regular.ttf",
]
PER_DIGIT = 2500
BATCH = 128
EPOCHS = 12


def load_fonts():
    fonts = []
    for d in FONT_DIRS:
        for fn in FONT_FILES:
            p = os.path.join(d, fn)
            if os.path.exists(p):
                for size in (34, 40, 46):
                    fonts.append(ImageFont.truetype(p, size))
    return fonts


def render_digit(digit, font, fg=0, bg=255):
    """渲染单个数字到 32×48 画布（黑字白底）。"""
    canvas = Image.new("L", (INPUT_W, INPUT_H), bg)
    d = ImageDraw.Draw(canvas)
    # 用蒙版画居中（粗略 bbox）
    tmp = Image.new("L", (INPUT_W * 2, INPUT_H * 2), bg)
    dt = ImageDraw.Draw(tmp)
    text = str(digit)
    bbox = dt.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (tmp.width - w) // 2 - bbox[0]
    y = (tmp.height - h) // 2 - bbox[1]
    dt.text((x, y), text, fill=fg, font=font)
    tmp = tmp.resize((INPUT_W, INPUT_H), Image.LANCZOS)
    return np.asarray(tmp, dtype=np.float32)


def augment(arr):
    """随机扰动（仿射/平移/缩放/模糊/噪声/亮度/横带）。"""
    a = arr.copy()
    H, W = a.shape
    # 平移 ±4px
    dx, dy = random.randint(-4, 4), random.randint(-4, 4)
    a = np.roll(np.roll(a, dx, axis=1), dy, axis=0)
    if dx > 0:
        a[:, :dx] = 255
    elif dx < 0:
        a[:, dx:] = 255
    if dy > 0:
        a[:dy, :] = 255
    elif dy < 0:
        a[dy:, :] = 255
    # 缩放 0.9-1.1（中心）
    sc = random.uniform(0.9, 1.1)
    import cv2
    nh, nw = int(H * sc), int(W * sc)
    nh, nw = min(nh, H), min(nw, W)
    r = cv2.resize(a, (nw, nh), interpolation=cv2.INTER_AREA)
    canvas = np.full((H, W), 255, dtype=np.float32)
    y0 = (H - nh) // 2
    x0 = (W - nw) // 2
    canvas[y0:y0 + nh, x0:x0 + nw] = r
    a = canvas
    # 仿射旋转 ±5°（cv2 绕中心）
    ang = random.uniform(-5, 5)
    M = cv2.getRotationMatrix2D((W / 2, H / 2), ang, 1.0)
    a = cv2.warpAffine(a, M, (W, H), flags=cv2.INTER_LINEAR,
                       borderValue=255)
    # 亮度/对比
    a = a * random.uniform(0.8, 1.15)
    a = np.clip(a, 0, 255)
    # 模糊
    if random.random() < 0.5:
        k = random.choice([1, 3, 5])
        a = cv2.GaussianBlur(a, (k, k), 0)
    # 噪声
    if random.random() < 0.5:
        a = a + np.random.normal(0, random.uniform(5, 18), a.shape)
    # 横带遮挡（博主红带/蓝带盖住数字部分）
    if random.random() < 0.25:
        y = random.randint(0, H - 8)
        a[y:y + random.randint(4, 10), :] = 255
    return np.clip(a, 0, 255)


class DigitSet(Dataset):
    def __init__(self, data, labels):
        self.data = data
        self.labels = labels

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, i):
        x = self.data[i].astype(np.float32)[None] / 255.0
        y = self.labels[i]
        return torch.from_numpy(x), y


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    fonts = load_fonts()
    if not fonts:
        print("[train] ERROR: 无可用字体")
        sys.exit(2)
    print(f"[train] 字体 {len(fonts)} 个，开始生成 {PER_DIGIT*10} 样本...")
    data, labels = [], []
    for digit in range(10):
        base = [render_digit(digit, f) for f in fonts]
        for _ in range(PER_DIGIT):
            arr = random.choice(base)
            data.append(augment(arr))
            labels.append(digit)
    data = np.stack(data)
    labels = np.array(labels, dtype=np.int64)
    print(f"[train] 数据集 {data.shape}")

    # 切分
    idx = np.random.permutation(len(labels))
    nval = 2000
    val_idx, tr_idx = idx[:nval], idx[nval:]
    tr = DigitSet(data[tr_idx], labels[tr_idx])
    va = DigitSet(data[val_idx], labels[val_idx])
    tr_loader = DataLoader(tr, batch_size=BATCH, shuffle=True, num_workers=0)
    va_loader = DataLoader(va, batch_size=BATCH, shuffle=False, num_workers=0)

    model = DigitCNN()
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    crit = nn.CrossEntropyLoss()
    best = None
    for ep in range(EPOCHS):
        model.train()
        tot, ok = 0, 0
        for x, y in tr_loader:
            opt.zero_grad()
            out = model(x)
            loss = crit(out, y)
            loss.backward()
            opt.step()
            ok += (out.argmax(1) == y).sum().item()
            tot += len(y)
        model.eval()
        vt, vo = 0, 0
        with torch.no_grad():
            for x, y in va_loader:
                out = model(x)
                vo += (out.argmax(1) == y).sum().item()
                vt += len(y)
        vacc = vo / vt
        print(f"[train] ep{ep+1}: train_acc={ok/tot:.4f} val_acc={vacc:.4f}")
        if best is None or vacc > best[0]:
            best = (vacc, ep, save_model(model))
    print(f"[train] best val_acc={best[0]:.4f} @ep{best[1]} -> {best[2]}")


if __name__ == "__main__":
    main()
