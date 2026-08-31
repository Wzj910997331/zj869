#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
image_recognize 公共模块：路径约定 / 博主名归一化 / JSON 落盘 / 命中判定。

独立性声明：本模块只依赖标准库 + numpy，不 import 主包任何东西。
所有产物写到 data/recognize/{blogger}/{date}/，主流水线文件零改动。
"""
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# /data/zhenjie/zj869
IMAGES_BASE = os.path.join(REPO, "data", "crawl", "{date}", "images")
RECOGNIZE_BASE = os.path.join(REPO, "data", "recognize")

POS_NAMES = {0: "万位", 1: "千位", 2: "百位", 3: "十位", 4: "个位"}
POS_MAP = {"万位": 0, "千位": 1, "百位": 2, "十位": 3, "个位": 4, "头": 0, "尾": 4}
# 走势图 5 列中心实测值（万/千/百/十/个）
COLUMN_TEMPLATE = [348, 499, 648, 798, 949]
VALID_TYPES = {"定位", "斜连", "胆码", "头", "尾", "和值", "杀号", "数字串", "其他"}
IMG_TYPES = {"走势图圈选", "杀号表", "文字预测截图", "其他"}


def fix_print():
    """修复 GBK 终端 print 崩溃。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def normalize_blogger(name):
    """去掉私有区/控制字符（如 '小屁股_483847515' 开头的 U+E04E）后 trim。"""
    if not name:
        return ""
    return "".join(
        ch for ch in str(name)
        if not (0xE000 <= ord(ch) <= 0xF8FF or ord(ch) < 0x20 or ch == "\x7f")
    ).strip()


def parse_position(pm):
    """把 position 字段（万位/千位/头/尾/"第N位"等）解析为 0-4，失败返回 None。"""
    if pm is None:
        return None
    pm = str(pm).strip()
    if pm in POS_MAP:
        return POS_MAP[pm]
    m = re.search(r"第?\s*([1-5])\s*[位]", pm)
    if m:
        return int(m.group(1)) - 1
    return None


def load_json(path):
    """宽容读 JSON：GBK/UTF-8 BOM 兜底，失败返回 None。"""
    if not path or not os.path.exists(path):
        return None
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return json.loads(raw.decode(enc))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return None


def write_json(obj, path):
    """原子落盘：ensure_ascii=False + indent=1，写 *.tmp 再 os.replace。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return path


def hit_record(rec, draw):
    """对给定开奖 draw 判定一条规律记录是否命中（镜像 summarize_image_patterns 逻辑）。

    draw: list[int] 长度5，如 26230=[9,4,6,8,3]。
    type ∈ 杀号/定位/头/尾/胆码/和值/数字串/其他。
    """
    if not draw or len(draw) < 5:
        return False
    t = rec.get("type", "")
    nums = [int(x) for x in (rec.get("numbers") or []) if str(x).isdigit()]
    if not nums:
        return False
    pos = parse_position(rec.get("position"))

    if t == "杀号":
        return all(n not in draw for n in nums)
    if t == "和值":
        s = sum(draw)
        for n in nums:
            if n == s or (n % 10 == s % 10 and 10 <= n <= 99 and n // 10 + n % 10 == s):
                pass
        # 简化：和值=个位和，含 a*10+b（a+b=s 或 b==s）
        if s in nums:
            return True
        for n in nums:
            if 10 <= n <= 99 and n // 10 + n % 10 == s:
                return True
        return False
    if t == "数字串":
        return any(n in draw for n in nums)
    if t in ("定位", "头", "尾", "胆码"):
        if pos is None:
            return False
        return draw[pos] in nums
    return False


def run_hits(records, draw):
    """批量附 hit 字段，返回新列表。"""
    out = []
    for r in records:
        r2 = dict(r)
        r2["hit"] = hit_record(r2, draw)
        out.append(r2)
    return out
