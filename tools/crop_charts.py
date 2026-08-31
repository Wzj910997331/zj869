# -*- coding: utf-8 -*-
"""走势图裁剪工具：把博主图表切出预测行区域，供视觉模型快速精确识别。

模式：
  bottom45  裁剪底部45%（覆盖 26228/26229/26230 多行，含列位校准行），并缩小到 <=maxw 宽
  zoom      按 y0/y1 像素区间裁剪目标行区域，放大 scale 倍（列位歧义时用）
  full      整图（小图直接送视觉，如 <=1300px 高的图）

用法：
  python tools/crop_charts.py --src DIR --dst DIR [--files f1.jpg f2.jpg | --list list.txt]
                              [--mode bottom45|zoom|full] [--maxw 700]
                              [--zoom-y0 880 --zoom-y1 1420] [--scale 1.5] [--suffix _b45s]
默认：bottom45 模式，src/dst 取命令行，无参数时打印帮助。
"""
import argparse
import os
import sys

from PIL import Image


def crop_bottom45(img, maxw, suffix):
    """底部45% + 缩小到 <=maxw 宽。返回 (输出文件名, 实际保存的图)。"""
    w, h = img.size
    y0 = int(h * 0.55)
    crop = img.crop((0, y0, w, h))
    if crop.size[0] > maxw:
        nw = maxw
        nh = int(crop.size[1] * maxw / crop.size[0])
        crop = crop.resize((nw, nh), Image.LANCZOS)
    return suffix, crop


def crop_zoom(img, y0, y1, scale, suffix):
    """按像素区间裁剪并放大。"""
    w, h = img.size
    y1 = min(y1, h)
    crop = img.crop((0, y0, w, y1))
    if scale and scale != 1.0:
        nw = int(crop.size[0] * scale)
        nh = int(crop.size[1] * scale)
        crop = crop.resize((nw, nh), Image.LANCZOS)
    return suffix, crop


def crop_full(img, maxw, suffix):
    """整图，超过 maxw 宽则缩小。"""
    if img.size[0] > maxw:
        nw = maxw
        nh = int(img.size[1] * maxw / img.size[0])
        img = img.resize((nw, nh), Image.LANCZOS)
    return suffix, img


def main():
    ap = argparse.ArgumentParser(description="走势图裁剪工具")
    ap.add_argument("--src", default=r"C:\Users\zhenjie.wu\.dsh\work\gouli_jpg")
    ap.add_argument("--dst", default=r"C:\Users\zhenjie.wu\.dsh\work\gouli_crop")
    ap.add_argument("--files", nargs="*", default=None)
    ap.add_argument("--list", default=None, help="含文件名的文本文件，每行一个")
    ap.add_argument("--mode", default="bottom45", choices=["bottom45", "zoom", "full"])
    ap.add_argument("--maxw", type=int, default=700)
    ap.add_argument("--zoom-y0", type=int, default=880)
    ap.add_argument("--zoom-y1", type=int, default=1420)
    ap.add_argument("--scale", type=float, default=1.5)
    ap.add_argument("--suffix", default=None, help="输出文件名后缀（不含扩展名前的分隔）")
    args = ap.parse_args()

    files = args.files or []
    if args.list and os.path.exists(args.list):
        with open(args.list, encoding="utf-8") as f:
            files += [ln.strip() for ln in f if ln.strip()]

    if not files:
        print("用法: python tools/crop_charts.py --src DIR --dst DIR --files f1.jpg f2.jpg [--mode ...]")
        print("或 --list list.txt（每行一个文件名）")
        sys.exit(0)

    os.makedirs(args.dst, exist_ok=True)

    for f in files:
        src_path = os.path.join(args.src, f)
        if not os.path.exists(src_path):
            print("MISSING:", f)
            continue
        img = Image.open(src_path).convert("RGB")
        w, h = img.size

        if args.mode == "bottom45":
            suffix, out_img = crop_bottom45(img, args.maxw, args.suffix or "_b45s")
            out_path = os.path.join(args.dst, f.replace(".jpg", suffix + ".jpg"))
            out_img.save(out_path, "JPEG", quality=85)
            print(f"{f} {w}x{h} -> {out_img.size} (bottom45, maxw={args.maxw})")
        elif args.mode == "zoom":
            suffix, out_img = crop_zoom(img, args.zoom_y0, args.zoom_y1, args.scale, args.suffix or "_zoom")
            out_path = os.path.join(args.dst, f.replace(".jpg", suffix + ".jpg"))
            out_img.save(out_path, "JPEG", quality=92)
            print(f"{f} {w}x{h} -> {out_img.size} (zoom y={args.zoom_y0}-{min(args.zoom_y1, h)}, scale={args.scale})")
        elif args.mode == "full":
            suffix, out_img = crop_full(img, args.maxw, args.suffix or "_full")
            out_path = os.path.join(args.dst, f.replace(".jpg", suffix + ".jpg"))
            out_img.save(out_path, "JPEG", quality=92)
            print(f"{f} {w}x{h} -> {out_img.size} (full)")


if __name__ == "__main__":
    main()
