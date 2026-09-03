#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数字分类小 CNN：32×48 灰度输入，3×Conv(32/64/128)+GAP+Linear(10)，~94k 参数。

用途：走势图单元格数字识别（0-9）。训练数据 = train_digits.py 合成字体。
推理输出 (digit, confidence)；conf<0.6 或 top-2 接近 → 上层标 uncertain。
"""
import os

import numpy as np
import torch
import torch.nn as nn

MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "digit_cnn.pt")
INPUT_H, INPUT_W = 32, 48
CONF_UNCERTAIN = 0.60
CONF_GAP = 0.15  # top1-top2 置信差 < 此值 → uncertain


class DigitCNN(nn.Module):
    def __init__(self):
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(1, 32, 3, padding=1), nn.ReLU(inplace=True), nn.MaxPool2d(2),
            nn.Conv2d(32, 64, 3, padding=1), nn.ReLU(inplace=True), nn.MaxPool2d(2),
            nn.Conv2d(64, 128, 3, padding=1), nn.ReLU(inplace=True),
        )
        self.head = nn.Sequential(
            nn.AdaptiveAvgPool2d(1),
            nn.Flatten(),
            nn.Dropout(0.3),
            nn.Linear(128, 10),
        )

    def forward(self, x):
        return self.head(self.features(x))


def load_model(device="cpu", path=None):
    """加载权重。path 默认 MODEL_PATH(digit_cnn.pt)；传入其它 .pt 可复用本架构(如 hand_cnn)。"""
    if path is None:
        path = MODEL_PATH
    if not os.path.exists(path):
        return None
    model = DigitCNN()
    try:
        model.load_state_dict(torch.load(path, map_location=device))
    except Exception as e:
        print(f"[digit_cnn] 模型加载失败({os.path.basename(path)}): {e}")
        return None
    model.to(device)
    model.eval()
    return model


def save_model(model):
    torch.save(model.state_dict(), MODEL_PATH)
    return MODEL_PATH


def _to_tensor(gray, device="cpu"):
    """灰度图（uint8 0-255 或 float 0-1）→ (1,1,32,48) tensor。

    背景用白色(1.0)填充: 走势图单元格是浅色底+深色数字, 白填充延续图表背景,
    与真实训练分布(render 白底/真实 cell 白底)一致。黑填充=给输入套黑框,
    训练分布从未见过, 会让小 CNN 误判(曾实测干净数字全判 0)。
    """
    if isinstance(gray, np.ndarray):
        if gray.dtype == np.uint8:
            arr = gray.astype(np.float32) / 255.0
        else:
            arr = gray.astype(np.float32)
    else:
        arr = np.asarray(gray, dtype=np.float32)
    if arr.ndim == 3:
        arr = arr[:, :, 0]
    h, w = arr.shape
    # 等比缩放填到 32×48 画布（保持长宽比，居中，白底）
    scale = min(INPUT_H / h, INPUT_W / w)
    nh, nw = max(1, int(round(h * scale))), max(1, int(round(w * scale)))
    canvas = np.full((INPUT_H, INPUT_W), 1.0, dtype=np.float32)
    import cv2
    r = cv2.resize(arr, (nw, nh), interpolation=cv2.INTER_AREA)
    y0 = (INPUT_H - nh) // 2
    x0 = (INPUT_W - nw) // 2
    canvas[y0:y0 + nh, x0:x0 + nw] = r
    t = torch.from_numpy(canvas[None, None]).float().to(device)
    return t


def predict(gray, model=None, device="cpu"):
    """gray: (H,W) 灰度。返回 (digit:int, conf:float, probs:np.ndarray) 或 None。"""
    if model is None:
        model = load_model(device)
    if model is None:
        return None
    with torch.no_grad():
        x = _to_tensor(gray, device)
        logits = model(x)[0]
        probs = torch.softmax(logits, dim=0).cpu().numpy()
    order = np.argsort(probs)[::-1]
    digit = int(order[0])
    conf = float(probs[order[0]])
    return digit, conf, probs


def uncertain_decision(conf, probs):
    """返回 (digit, conf, uncertain)。"""
    if probs is None:
        return None, 0.0, True
    order = np.argsort(probs)[::-1]
    top1, top2 = float(probs[order[0]]), float(probs[order[1]])
    uncertain = (top1 < CONF_UNCERTAIN) or ((top1 - top2) < CONF_GAP)
    return int(order[0]), top1, uncertain


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from common import fix_print
    fix_print()
    model = load_model()
    if model is None:
        print("模型未训练：请先跑 train_digits.py")
        sys.exit(1)
    print(f"模型就绪: {MODEL_PATH}")
    print(f"参数量: {sum(p.numel() for p in model.parameters()):,}")
