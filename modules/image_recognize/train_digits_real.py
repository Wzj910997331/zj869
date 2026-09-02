#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
train_digits_real.py — 用真实走势图单元格训练数字 CNN → model/digit_cnn.pt

数据: build_digit_dataset.py 产出的真实标注数字集(开奖历史 + 期号列自标注)。
  期号位: 底部期号列连通域游程(最左紧组) + tesseract 已匹配期号做真值;
  结果位: detect_columns 定位 5 结果列 + 开奖真值, 按 box 中心与列中心偏移 ≤12px 过滤。
  已按宽高比 0.35-1.5 / 暗像素占比 3%-65% 清洗。

训练: 轻度增强(平移±3/缩放0.9-1.1/亮度/横带遮挡/轻微模糊), 分层留出 20% 评估。
  合成字体方案(原 train_digits.py)在同分布重采样上只到 44.7% 且干净数字全判 0,
  判定不可用; 本脚本只用真实 cell, 直接贴合推理分布。

用法:
  /usr/bin/python3 modules/image_recognize/train_digits_real.py [--npz path] [--epochs 40]
"""
import argparse
import os
import random
import sys

import cv2
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from model.digit_cnn import DigitCNN, _to_tensor, save_model  # noqa: E402

random.seed(7)
np.random.seed(7)
torch.manual_seed(7)

DEFAULT_NPZ = "/tmp/digit_real_final.npz"


def augment(cell):
    """轻度扰动(真实数字已自带字体/遮挡/网格变化, 增强要克制)。

    实测: 横带遮挡 8px/平移±3 在小 cell(~25px)上过于破坏, 模型学到 ~40% 就上不去;
    收窄到平移±2 / 带 2-4px / 概率降低。
    """
    a = cell.astype(np.float32).copy()
    h, w = a.shape
    # 平移 ±2px
    dx, dy = random.randint(-2, 2), random.randint(-2, 2)
    a = np.roll(np.roll(a, dx, axis=1), dy, axis=0)
    if dx > 0:
        a[:, :dx] = 255
    elif dx < 0:
        a[:, dx:] = 255
    if dy > 0:
        a[:dy, :] = 255
    elif dy < 0:
        a[dy:, :] = 255
    # 缩放 0.95-1.05
    sc = random.uniform(0.95, 1.05)
    nh, nw = max(1, int(h * sc)), max(1, int(w * sc))
    nh, nw = min(nh, h), min(nw, w)
    r = cv2.resize(a, (nw, nh), interpolation=cv2.INTER_AREA)
    canvas = np.full((h, w), 255, dtype=np.float32)
    y0, x0 = (h - nh) // 2, (w - nw) // 2
    canvas[y0:y0 + nh, x0:x0 + nw] = r
    a = canvas
    # 亮度/对比
    a = a * random.uniform(0.9, 1.1)
    # 轻微模糊
    if random.random() < 0.15:
        a = cv2.GaussianBlur(a, (3, 3), 0)
    # 横带遮挡(博主红/蓝带盖数字): 只盖 2-4px, 概率降
    if random.random() < 0.10:
        y = random.randint(0, max(1, h - 5))
        a[y:y + random.randint(2, 4), :] = 255
    return np.clip(a, 0, 255).astype(np.uint8)


class CellSet(Dataset):
    def __init__(self, cells, labels, augment_on_read):
        self.cells = cells
        self.labels = labels
        self.aug = augment_on_read

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, i):
        cell = self.cells[i]
        if self.aug:
            cell = augment(cell)
        x = _to_tensor(cell)[0]  # 去掉 batch 维, 交由 DataLoader 组 batch
        return x, self.labels[i]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--npz", default=DEFAULT_NPZ)
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--lr", type=float, default=3e-3)
    ap.add_argument("--no-aug", action="store_true", help="关闭增强(调试)")
    args = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    torch.set_num_threads(4)

    d = np.load(args.npz, allow_pickle=True)
    cells = list(d["data"])
    labels = d["labels"]
    meta = d["meta"]
    print(f"[train] 加载 {len(cells)} 个真实 cell")

    # 分层切分(80/20)
    tr_idx, va_idx = [], []
    for lab in range(10):
        idx = [i for i, l in enumerate(labels) if int(l) == lab]
        random.shuffle(idx)
        nv = max(2, int(len(idx) * 0.2))
        va_idx += idx[:nv]
        tr_idx += idx[nv:]
    tr = CellSet([cells[i] for i in tr_idx], labels[tr_idx], not args.no_aug)
    va = CellSet([cells[i] for i in va_idx], labels[va_idx], False)
    # 类别均衡采样: 周期位数字偏 2/3/6, 稀有类(0,1,4,5,7,8,9)被淹没 → 按 1/类频 加权
    counts = np.bincount(labels[tr_idx], minlength=10).astype(np.float64)
    weights = np.where(counts[labels[tr_idx]] > 0, 1.0 / counts[labels[tr_idx]], 0.0)
    sampler = torch.utils.data.WeightedRandomSampler(weights, len(tr_idx), replacement=True)
    tr_loader = DataLoader(tr, batch_size=args.batch, sampler=sampler)
    va_loader = DataLoader(va, batch_size=args.batch, shuffle=False)
    print(f"[train] 训练 {len(tr_idx)} / 留出 {len(va_idx)}")

    model = DigitCNN()
    # 真实集只有 ~500 样本, Dropout 0.3 会吃掉训练信号(实测小样本 CNN 全崩);
    # 降到 0.1 保留轻微正则。推理 eval 模式不受影响。
    for mod in model.modules():
        if isinstance(mod, nn.Dropout):
            mod.p = 0.1
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    crit = nn.CrossEntropyLoss()
    best = None
    for ep in range(args.epochs):
        model.train()
        tot = ok = 0
        for x, y in tr_loader:
            opt.zero_grad()
            loss = crit(model(x), y)
            loss.backward()
            opt.step()
            ok += (model(x).argmax(1) == y).sum().item()
            tot += len(y)
        model.eval()
        vok = vtot = 0
        with torch.no_grad():
            for x, y in va_loader:
                vok += (model(x).argmax(1) == y).sum().item()
                vtot += len(y)
        vacc = vok / vtot
        print(f"[train] ep{ep+1}: train_acc={ok/tot:.4f} val_acc={vacc:.4f}", flush=True)
        if best is None or vacc > best[0]:
            best = (vacc, ep, save_model(model))

    print(f"\n[train] best val_acc={best[0]:.4f} @ep{best[1]} -> {best[2]}")

    # 留出集每类精度
    conf = {}
    with torch.no_grad():
        for i in va_idx:
            x = _to_tensor(cells[i])
            p = model(x)[0].argmax().item()
            conf.setdefault(int(labels[i]), [0, 0])[1] += 1
            conf[int(labels[i])][0] += (p == int(labels[i]))
    print("[train] 留出集每类精度 (正确/总数):")
    for lab in sorted(conf):
        okc, totc = conf[lab]
        print(f"  {lab}: {okc}/{totc} = {okc/totc:.2f}")
    print(f"\n[train] 全部留出 {len(va_idx)} 个: "
          f"{sum(v[0] for v in conf.values())}/{len(va_idx)} = "
          f"{sum(v[0] for v in conf.values())/len(va_idx):.3f}")


if __name__ == "__main__":
    main()
