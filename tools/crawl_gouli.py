#!/usr/bin/env python3
"""
够力论坛爬虫：爬取指定日期内排列五（lottery=2）的全部帖子（文字+图片）。
- 数据源: https://wsqdata.gouli8.cn/v2/feeds/stream (公开接口，无需登录)
- 输出: data/crawl/{YYYYMMDD}/posts.json + images/*.img + images_map.json
- 用法: python tools/crawl_gouli.py [YYYY-MM-DD]   (默认 2026-08-28)
"""
import datetime
import json
import os
import sys
import time
import urllib.request

BASE = "https://wsqdata.gouli8.cn"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
LOTTERY = 2  # 排列五


def http_get(url, timeout=30):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": "https://www.gouli99.cn/",
    })
    return urllib.request.urlopen(req, timeout=timeout).read()


def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else "2026-08-28"
    cutoff = datetime.datetime.strptime(date_str + " 00:00:00", "%Y-%m-%d %H:%M:%S")
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "data", "crawl", date_str.replace("-", ""))
    img_dir = os.path.join(out_dir, "images")
    os.makedirs(img_dir, exist_ok=True)

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
            if t >= cutoff:
                posts.append(it)
        print(f"start={start} got={len(items)} oldest={oldest}")
        if not data.get("hasNext") or oldest is None or oldest < cutoff:
            break
        start += count
        time.sleep(0.3)

    posts.sort(key=lambda p: p.get("create_time", ""))
    posts_json = os.path.join(out_dir, "posts.json")
    with open(posts_json, "w", encoding="utf-8") as f:
        json.dump(posts, f, ensure_ascii=False, indent=1)
    print("saved:", posts_json)

    # 下载图片（origin 原图优先）
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
    print("=" * 40)
    print("=== SUMMARY ===")
    print("date:", date_str, "| posts:", len(posts), "| bloggers:", len(bloggers), "| images:", len(img_map))
    for n, c in sorted(bloggers.items(), key=lambda x: -x[1]):
        print(f"  {c:3d}  {n}")


if __name__ == "__main__":
    main()
