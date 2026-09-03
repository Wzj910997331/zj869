#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""train_hand_mnist.py — 博主手写数字分类器的 MNIST 强基线（2026-09-03 起）。

背景：博主在走势图裁剪条里的手写单码（A 类）是非印刷体，印刷期号训出的 digit_cnn.pt
读不出（blogger_hit_gate docstring 明言）。这里用 MNIST（人手写 0-9，6 万张）在
DigitCNN 同一架构上训一个通用手写基线 → model/hand_cnn.pt（~94k 参数，CPU <1min ≈99%）。

与 digit_cnn 的关系：
  - 同一 DigitCNN 架构 / 同一 32×48 白底等比输入（_to_tensor）→ load_model(path=hand_cnn.pt)
    即可换权重走同一 API，集成零摩擦。
  - 本文件=纯 MNIST 基线；真实博主字形（26231 人工 A + 26230 DS 自举）的微调见
    build_hand_glyphs.py 产 npz → fine-tune_hand.py（下一步）。

用法：python3 modules/image_recognize/train_hand_mnist.py [--epochs 6] [--out model/hand_cnn.pt]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "model"))

import torch
import torch.nn as nn
from torch.utils.data import DataLoader

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MNIST_ROOT = os.path.join(REPO, ".cache", "mnist")

from digit_cnn import DigitCNN, INPUT_H, INPUT_W, _to_tensor  # noqa: E402


def load_mnist(root=MNIST_ROOT, train=True):
    """读 torchvision MNIST（raw 已由外部脚本下载到 root/MNIST/raw/）。"""
    from torchvision import datasets
    ds = datasets.MNIST(root=root, train=train, download=False)
    imgs = ds.data.numpy()          # (N,28,28) uint8
    labels = ds.targets.numpy()
    return imgs, labels


def to_model_input(imgs):
    """MNIST (N,28,28) uint8 → (N,1,32,48) float 张量。

    复刻 digit_cnn._to_tensor 的白底等比缩放（容器/无 cv2 场景用 PIL 实现，等价即可；
    服务端推理仍用 digit_cnn._to_tensor 的 cv2 路径，训练/推理输入分布一致）。"""
    from PIL import Image as PILImage
    outs = np.empty((len(imgs), INPUT_H, INPUT_W), dtype=np.float32)
    for i in range(len(imgs)):
        im = PILImage.fromarray(imgs[i]).convert("L")
        h, w = im.size[1], im.size[0]
        scale = min(INPUT_H / h, INPUT_W / w)
        nh, nw = max(1, round(h * scale)), max(1, round(w * scale))
        if (nw, nh) != (w, h):
            im = im.resize((nw, nh), PILImage.BILINEAR)
        canvas = np.full((INPUT_H, INPUT_W), 1.0, dtype=np.float32)
        y0, x0 = (INPUT_H - nh) // 2, (INPUT_W - nw) // 2
        canvas[y0:y0 + nh, x0:x0 + nw] = np.asarray(im, dtype=np.float32) / 255.0
        outs[i] = canvas
    return torch.from_numpy(outs[:, None])


def train(model, x, y, xv, yv, epochs, lr, batch, device):
    model.to(device)
    x, y, xv, yv = x.to(device), y.to(device), xv.to(device), yv.to(device)
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    crit = nn.CrossEntropyLoss()
    for ep in range(1, epochs + 1):
        model.train()
        perm = torch.randperm(len(x))
        tot, corr, loss_acc = 0, 0, 0.0
        for s in range(0, len(x), batch):
            idx = perm[s:s + batch]
            xb, yb = x[idx], y[idx]
            opt.zero_grad()
            out = model(xb)
            loss = crit(out, yb)
            loss.backward()
            opt.step()
            loss_acc += loss.item() * len(idx)
            tot += len(idx)
            corr += (out.argmax(1) == yb).sum().item()
        tr_acc = corr / tot
        model.eval()
        with torch.no_grad():
            va = (model(xv).argmax(1) == yv).float().mean().item()
        print(f"  epoch {ep}/{epochs}  loss {loss_acc/tot:.4f}  train_acc {tr_acc:.4f}  val_acc {va:.4f}", flush=True)
    return model


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=6)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--batch", type=int, default=128)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  "model", "hand_cnn.pt"))
    ap.add_argument("--threads", type=int, default=4,
                    help="torch 线程数。小网络高线程反而慢（每 op 线程池开销）；默认 4 即可。")
    ap.add_argument("--device", default=("cuda" if torch.cuda.is_available() else "cpu"))
    args = ap.parse_args()
    torch.set_num_threads(args.threads)

    print("[mnist] 加载训练集…")
    im, lb = load_mnist(train=True)
    x = to_model_input(im)
    y = torch.from_numpy(lb).long()
    print(f"[mnist] train {x.shape[0]} 张")

    print("[mnist] 加载测试集…")
    imv, lbv = load_mnist(train=False)
    xv = to_model_input(imv)
    yv = torch.from_numpy(lbv).long()
    print(f"[mnist] test  {xv.shape[0]} 张")

    model = DigitCNN()
    print(f"[mnist] 参数量 {sum(p.numel() for p in model.parameters()):,}  device={args.device}")
    train(model, x, y, xv, yv, args.epochs, args.lr, args.batch, args.device)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    torch.save(model.state_dict(), args.out)
    print(f"[mnist] 权重已存 {args.out}  ({os.path.getsize(args.out)/1e6:.2f} MB)")


if __name__ == "__main__":
    main()
