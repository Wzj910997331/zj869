#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
识别完成后清理 error 条目（timed out/empty/HTTP xxx），便于 driver resume 重识别。
用法: python tools/strip_errors.py 20260829 [20260830 ...]   # base 为裸目录名
效果: 从 vision_patterns_full.json 移除带 error 字段的条目并落盘；
      驱动/识别脚本的 resume 会把它们当作"未完成"重新识别。
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

for base in sys.argv[1:]:
    p = os.path.join(REPO, "data", "crawl", base, "vision_patterns_full.json")
    if not os.path.exists(p):
        print(f"!! {base}: {p} 不存在，跳过")
        continue
    data = json.load(open(p, encoding="utf-8"))
    errs = [v for v in data if v.get("error")]
    kept = [v for v in data if not v.get("error")]
    if not errs:
        print(f"{base}: 无 error 条目，无需清理")
        continue
    json.dump(kept, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    dist = {}
    for v in errs:
        dist[v["error"]] = dist.get(v["error"], 0) + 1
    print(f"{base}: 移除 error 条目 {len(errs)} 条（{dist}）→ 保留 {len(kept)} 条")
    for v in errs[:3]:
        print(f"   - {v['file']}  [{v['error']}]")
    if len(errs) > 3:
        print(f"   ... 共 {len(errs)} 条")
