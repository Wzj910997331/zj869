#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
train_digits_preaug.py — 预增强固定集训练数字 CNN（比 train_digits_real 快且稳）

问题: train_digits_real 每 epoch 对全部训练 cell 做一遍实时增强, ~1930 cell 时
每 epoch 增足够 15-20s, 120 epoch = 30min+; 且实时增强+类均衡采样(稀有类重复+增强)
使同一 cell 每 epoch 目标不同, 小模型拟合慢(merged 923 跑到 ep49 train_acc 才 0.67)。

方案: 一次性把每类增强到固定样本数(稀有类多增强/常见类少增强), 得到固定 (X,y),
普通 shuffle 训练 → 每 epoch 无增强开销, 目标稳定, 收敛快。分层留出 20% 原 cell
评估(评估 sample 绝不进入训练增强), 与真实推理分布一致。

用法:
  /usr/bin/python3 modules/image_recognize/train_digits_preaug.py \
      --npz /tmp/digit_merge3.npz --per-class 700 --epochs 80 [--no-aug-eval]
"""
import argparse
import os
import random
import sys

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from model.digit_cnn import DigitCNN, _to_tensor, save_model  # noqa: E402
from train_digits_real import augment  # noqa: E402

random.seed(7)
np.random.seed(7)
torch.manual_seed(7)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--npz", required=True)
    ap.add_argument("--per-class", type=int, default=700)
    ap.add_argument("--epochs", type=int, default=80)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--lr", type=float, default=3e-3)
    args = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    torch.set_num_threads(4)

    d = np.load(args.npz, allow_pickle=True)
    cells = list(d["data"])
    labels = d["labels"].astype(np.int64)
    print(f"[preaug] 加载 {len(cells)} 真实 cell  ({os.path.basename(args.npz)})", flush=True)

    # 分层留出 20% 原 cell
    tr_cells, tr_lab = [], []
    va_cells, va_lab = [], []
    for lab in range(10):
        li = [i for i, l in enumerate(labels) if int(l) == lab]
        random.shuffle(li)
        nv = max(3, int(len(li) * 0.2))
        for i in li[:nv]:
            va_cells.append(cells[i]); va_lab.append(lab)
        for i in li[nv:]:
            tr_cells.append(cells[i]); tr_lab.append(lab)
    va_lab = np.array(va_lab, dtype=np.int64)
    print(f"[preaug] 训练 {len(tr_cells)} / 留出 {len(va_cells)}", flush=True)

    # 每类增强到 per_class 固定样本(稀有类复用+增强)
    tr_lab = np.array(tr_lab)
    Xt_list, y = [], []
    for lab in range(10):
        idx = np.where(tr_lab == lab)[0]
        n = len(idx)
        if n == 0:
            continue
        reps = int(np.ceil(args.per_class / n))
        for _ in range(reps):
            for i in idx:
                Xt_list.append(_to_tensor(augment(tr_cells[i])))
                y.append(lab)
    Xt = torch.cat(Xt_list, dim=0)
    y = np.array(y, dtype=np.int64)
    print(f"[preaug] 增强后训练集 {tuple(Xt.shape)} 类分布 "
          f"{dict(zip(*np.unique(y, return_counts=True)))}", flush=True)

    tl = DataLoader(TensorDataset(Xt, torch.from_numpy(y)), batch_size=args.batch,
                    shuffle=True, num_workers=0)
    vl = DataLoader(TensorDataset(torch.cat([_to_tensor(c) for c in va_cells], dim=0),
                                  torch.from_numpy(va_lab)), batch_size=args.batch)

    model = DigitCNN()
    for mod in model.modules():
        if isinstance(mod, nn.Dropout):
            mod.p = 0.1
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    crit = nn.CrossEntropyLoss()
    best = None
    for ep in range(args.epochs):
        model.train()
        tot = ok = 0
        for x, yb in tl:
            opt.zero_grad()
            out = model(x)
            loss = crit(out, yb)
            loss.backward()
            opt.step()
            ok += (out.argmax(1) == yb).sum().item()
            tot += len(yb)
        model.eval()
        with torch.no_grad():
            voks = torch.cat([model(x).argmax(1) for x, _ in vl])
        vacc = (voks == va_lab).float().mean().item()
        print(f"[preaug] ep{ep+1}: train_acc={ok/tot:.4f} val_acc={vacc:.4f}", flush=True)
        if best is None or vacc > best[0]:
            best = (vacc, ep, save_model(model))

    print(f"\n[preaug] best val_acc={best[0]:.4f} @ep{best[1]} -> {best[2]}", flush=True)
    conf = {}
    for i, (pred, lab) in enumerate(zip(voks.tolist(), va_lab.tolist())):
        conf.setdefault(lab, [0, 0])[1] += 1
        conf[lab][0] += (pred == lab)
    print("[preaug] 留出集每类精度 (正确/总数):")
    for lab in sorted(conf):
        a, b = conf[lab]
        print(f"  {lab}: {a}/{b} = {a/b:.2f}")


if __name__ == "__main__":
    main()
