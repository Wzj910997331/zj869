#!/usr/bin/env python3
"""
文字规律分析：从 posts.json 提取每条帖子的规律，分类统计、去重计数。
输出: data/crawl/{date}/analysis_text.json + 打印摘要
"""
import json
import os
import re
import sys
from collections import Counter, defaultdict

DATE = sys.argv[1] if len(sys.argv) > 1 else "2026-08-28"
BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "crawl", DATE.replace("-", ""))
posts = json.load(open(os.path.join(BASE, "posts.json"), encoding="utf-8"))

POS_WORDS = {"万位": 0, "千位": 1, "百位": 2, "十位": 3, "个位": 4,
             "万": 0, "千": 1, "百": 2, "十": 3, "个": 4}

def classify_line(line):
    """把一行文本分类为规律类型，返回 (type, normalized) 或 None"""
    line = line.strip()
    if len(line) < 2:
        return None
    # 定位候选：如 "万位 3,7,1,0,6,8,2" / "百位:5,4,6,8,3,2,1" / "12345百"
    for pos in ["万位", "千位", "百位", "十位", "个位"]:
        if pos in line:
            m = re.search(pos + r"\s*[:：]?\s*([0-9,，\s]+)", line)
            if m:
                nums = re.findall(r"\d", m.group(1))
                return ("定位_" + pos, f"{pos}:{''.join(sorted(set(nums)))}")
    # 杀号
    if "杀" in line:
        m = re.search(r"杀\s*([0-9,，\s]+)", line)
        if m:
            nums = re.findall(r"\d", m.group(1))
            return ("杀号", "杀:" + "".join(sorted(set(nums))))
        return ("杀号", re.sub(r"[\s]", "", line)[:20])
    # 头/尾
    if re.search(r"[0-9]+\s*头", line) or "头:" in line or "头：" in line:
        m = re.search(r"([0-9,\s]+)\s*头", line)
        if m:
            return ("头", "头:" + re.sub(r"[\s,，]", "", m.group(1))[:20])
    if re.search(r"[0-9]+\s*尾", line) or "尾:" in line or "尾：" in line:
        m = re.search(r"([0-9,\s]+)\s*尾", line)
        if m:
            return ("尾", "尾:" + re.sub(r"[\s,，]", "", m.group(1))[:20])
    # 和值/跨度/大小/奇偶/质合
    for kw, t in [("和值", "和值"), ("跨度", "跨度"), ("大数", "大小"), ("小数", "大小"),
                  ("奇数", "奇偶"), ("偶数", "奇偶"), ("质数", "质合"), ("合数", "质合"),
                  ("连号", "连号"), ("重号", "重号"), ("间隔", "间隔")]:
        if kw in line:
            return (t, re.sub(r"[\s，。！？、]", "", line)[:24])
    # 纯数字串（可能是胆码/复式）
    if re.fullmatch(r"[0-9,\s\-—－]+", line) and len(re.findall(r"\d", line)) >= 3:
        return ("数字串", re.sub(r"[\s,，]", "", line)[:24])
    return None

# 统计
type_counter = Counter()
pattern_counter = Counter()   # (type, normalized) -> count
by_blogger = defaultdict(list)

for p in posts:
    creator = p.get("creator", {}).get("name", "?")
    content = p.get("content") or ""
    lines = [l.strip() for l in re.split(r"[\n\r]", content) if l.strip()]
    for line in lines:
        r = classify_line(line)
        if r:
            t, norm = r
            type_counter[t] += 1
            pattern_counter[(t, norm)] += 1
            by_blogger[creator].append((t, norm))

# 去重后规律数
unique_patterns = len(pattern_counter)
total_patterns = sum(pattern_counter.values())

result = {
    "date": DATE,
    "posts": len(posts),
    "total_pattern_lines": total_patterns,
    "unique_patterns": unique_patterns,
    "by_type": dict(type_counter),
    "top_patterns": [{"type": t, "pattern": n, "count": c}
                     for (t, n), c in pattern_counter.most_common(30)],
}
with open(os.path.join(BASE, "analysis_text.json"), "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=1)

print("=" * 50)
print(f"日期: {DATE} | 帖子: {len(posts)}")
print(f"规律行总数: {total_patterns} | 去重后规律: {unique_patterns}")
print("按类型分布:")
for t, c in type_counter.most_common():
    print(f"  {t:8s} {c}")
print("\nTOP 规律(去重后出现最多的):")
for p in result["top_patterns"][:20]:
    print(f"  [{p['type']}] {p['pattern']}  x{p['count']}")
