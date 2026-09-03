#!/usr/bin/env python3
"""
够力论坛爬虫：按 create_time 抓取排列五（lottery=2）的帖子（文字+图片），**按自然日分目录**落盘。

- 数据源: https://wsqdata.gouli8.cn/v2/feeds/stream (公开接口，无需登录)
- 输出: data/crawl/{YYYYMMDD}/posts.json + images/*.jpg + images_map.json
  - 只给一个日期：抓该自然日 [00:00:00, 23:59:59]，落 data/crawl/{该日}（上界恒封顶到当日 24 点，
    即使之后某天补爬也不会把次日帖子带进当日目录）。
  - 给 [start, end]：按每个自然日**各存一个目录**（不揉进 start 目录）。
- 每条帖子额外注解 explicit_period：正文**唯一**显式期号（如"排列五26233期"→'26233'；
  无或出现多个不同期号 = None）——供归期侧"时间切期 × 期号备注"交叉核验使用。
- 用法: python tools/crawl_gouli.py [YYYY-MM-DD] [YYYY-MM-DD(end,可选)]  (默认 2026-08-28)
"""
import datetime
import json
import os
import re
import sys
import time
import urllib.request
from collections import defaultdict

BASE = "https://wsqdata.gouli8.cn"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
LOTTERY = 2  # 排列五

# 正文显式期号："排列五26233期"/"26233期"；排除 5 位前紧邻数字（金额/编号）减少误命中。
# 与 tools/include_prevday_tail.py 的 PERIOD_NOTE_RE 保持一致。
PERIOD_NOTE_RE = re.compile(r"(?<!\d)(\d{5})(?!\d)\s*期")


def explicit_period(content):
    """正文唯一显式期号 → 5 位字符串；无 / 多个不同期号（如《4020》一串复盘期号）→ None。"""
    if not content:
        return None
    s = set(m.group(1) for m in PERIOD_NOTE_RE.finditer(str(content)))
    return s.pop() if len(s) == 1 else None


def http_get(url, timeout=30):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": "https://www.gouli99.cn/",
    })
    return urllib.request.urlopen(req, timeout=timeout).read()


def fetch_posts(cutoff, end_cutoff):
    """翻页拉取 create_time ∈ [cutoff, end_cutoff] 的全部帖子。"""
    start, count = 0, 20
    posts = []
    while True:
        url = f"{BASE}/v2/feeds/stream?start={start}&count={count}&lottery={LOTTERY}"
        try:
            data = json.loads(http_get(url))
        except Exception as e:
            print(f"[warn] start={start} fetch error: {e}; retry in 3s")
            time.sleep(3)
            continue
        items = data.get("items", [])
        if not items:
            print(f"start={start}: no items, stop")
            break
        oldest = None
        for it in items:
            ct = it.get("create_time", "")
            try:
                t = datetime.datetime.strptime(ct, "%Y-%m-%d %H:%M:%S")
            except Exception:
                continue
            if oldest is None or t < oldest:
                oldest = t
            if cutoff <= t <= end_cutoff:
                posts.append(it)
        print(f"start={start} got={len(items)} oldest={oldest}")
        if not data.get("hasNext") or oldest is None or oldest < cutoff:
            break
        start += count
        time.sleep(0.3)
    posts.sort(key=lambda p: p.get("create_time", ""))
    return posts


def save_day_dir(out_dir, posts):
    """把某一天的帖子写成 data/crawl/<day>/ 目录（posts.json + 下载图片 + images_map.json）。"""
    img_dir = os.path.join(out_dir, "images")
    os.makedirs(img_dir, exist_ok=True)
    # 注解正文显式期号，供"时间切期 × 期号备注"交叉核验
    for p in posts:
        if "explicit_period" not in p:
            p["explicit_period"] = explicit_period(p.get("content"))
    posts_json = os.path.join(out_dir, "posts.json")
    with open(posts_json, "w", encoding="utf-8") as f:
        json.dump(posts, f, ensure_ascii=False, indent=1)
    print("saved:", posts_json)

    img_map = {}
    for p in posts:
        pid = p.get("id", "unknown")
        for i, iu in enumerate(p.get("image_urls", []) or []):
            u = iu.get("origin") or iu.get("750") or iu.get("360")
            if not u:
                continue
            ext = os.path.splitext(u.split("?")[0])[1] or ".jpg"
            fn = f"{pid}_{i}{ext}"
            try:
                raw = http_get(u)
                with open(os.path.join(img_dir, fn), "wb") as f:
                    f.write(raw)
                img_map[fn] = u
                print("img:", fn, len(raw))
            except Exception as e:
                print("img fail:", fn, e)
            time.sleep(0.2)
    with open(os.path.join(out_dir, "images_map.json"), "w", encoding="utf-8") as f:
        json.dump(img_map, f, ensure_ascii=False, indent=1)

    bloggers = {}
    for p in posts:
        n = p.get("creator", {}).get("name", "?")
        bloggers[n] = bloggers.get(n, 0) + 1
    print(f"  day {os.path.basename(out_dir)}: posts={len(posts)} images={len(img_map)} "
          f"bloggers={len(bloggers)}")


def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else "2026-08-28"
    end_str = sys.argv[2] if len(sys.argv) > 2 else ""

    # 要爬的自然日列表 + 时间边界：下界=首日 00:00，上界=末日 23:59:59（恒封顶，单日也不例外）
    start_day = datetime.datetime.strptime(date_str, "%Y-%m-%d")
    last_day = (datetime.datetime.strptime(end_str, "%Y-%m-%d")
                if end_str else start_day)
    days = []
    cur = start_day
    while cur <= last_day:
        days.append(cur)
        cur += datetime.timedelta(days=1)
    cutoff = datetime.datetime(start_day.year, start_day.month, start_day.day, 0, 0, 0)
    end_cutoff = datetime.datetime(last_day.year, last_day.month, last_day.day, 23, 59, 59)

    posts = fetch_posts(cutoff, end_cutoff)
    print("=" * 40)
    print("=== fetch summary: range", cutoff, "→", end_cutoff, "| posts:", len(posts))

    # 按自然日分目录落盘（双日期不再揉进 start 目录）
    by_day = defaultdict(list)
    for p in posts:
        try:
            t = datetime.datetime.strptime(p.get("create_time", ""), "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
        by_day[t.strftime("%Y%m%d")].append(p)
    for day_dt in days:
        ymd = day_dt.strftime("%Y%m%d")
        out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "..", "data", "crawl", ymd)
        print(f"--- {ymd} ---")
        save_day_dir(out_dir, by_day.get(ymd, []))
    print("done. days:", [d.strftime("%Y%m%d") for d in days])


if __name__ == "__main__":
    main()
