#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_hit_gallery_26233.py — 命中规律图集 HTML（内嵌裁剪图 + 对应 json）。

对选定的命中图,把 02_annotated.png(标注行裁剪图)以 data URI 内嵌,
旁挂该图 analyze json(rows/patterns/checks/metrics),生成浏览器可打开的
docs/命中规律图集-20260831.html。
"""
import base64
import html
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATE = "20260831"
OUT_ROOT = os.path.join(REPO, "data", "recognize", f"{DATE}_all")
AN = json.load(open(os.path.join(OUT_ROOT, "analysis", f"analyze_{DATE}.json"),
                    encoding="utf-8"))["images"]
MAN = json.load(open(os.path.join(OUT_ROOT, "crops_all_manifest.json"),
                     encoding="utf-8"))["images"]
PAT = json.load(open(os.path.join(REPO, "data", "crawl", DATE,
                                  "image_patterns_with_blogger.json"),
                     encoding="utf-8"))

SELECT = [
    ("s_2_bcc9fbba-dde4-4852-95ce-d98377ab6dc3_1.jpg",
     "含26233行(自证) · 7条命中 · 命中最多"),
    ("s_2_ae0f0151-94ec-4adf-845e-e5083acf3177_0.jpg",
     "不含26233行(前瞻) · 4条命中 · 开奖前视角"),
    ("s_2_0e07c2be-59b0-468a-b1af-6f6bb0bdb8f6_3.jpg",
     "含26233行(自证) · 6条命中"),
]


def img_data_uri(path):
    with open(path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()


def esc(s):
    return html.escape(str(s))


def pattern_rows(f):
    out = []
    for p in PAT:
        if p["file"] != f:
            continue
        hit = "✓" if p["hit"] else "✗"
        cls = "hit" if p["hit"] else "miss"
        pos = p.get("position") or "—"
        nums = "".join(map(str, p["numbers"]))
        out.append(f'<tr class="{cls}"><td>{hit}</td><td>{esc(p["type"])}</td>'
                   f'<td>{esc(pos)}</td><td>{esc(nums)}</td><td>{esc(p["desc"])}</td></tr>')
    return "\n".join(out)


def row_html(f):
    r = AN[f]
    crop = MAN[f]
    cd = crop.get("crop_dir", "")
    img = os.path.join(OUT_ROOT, cd, "02_annotated.png")
    uri = img_data_uri(img) if os.path.exists(img) else ""
    checks = " ".join(f"{k}={'✓' if v['pass'] else '✗'}"
                      for k, v in r["checks"].items())
    m = r["metrics"]
    src = os.path.join("data/crawl", DATE, "images", f)
    rows = "\n".join(
        f"<tr><td>row{k}</td><td>{esc(v['period']) if v.get('period') else '—'}</td>"
        f"<td>{esc(' '.join(map(str, v['draw']))) if v.get('draw') else '—'}</td>"
        f"<td>{'✓' if v.get('matched') else '—'}</td></tr>"
        for k, v in sorted(r["rows"].items(), key=lambda kv: int(kv[0])))
    n_hit = sum(1 for p in PAT if p["file"] == f and p["hit"])
    return f"""
<section style="border:1px solid #ccc;border-radius:8px;padding:14px;margin:14px 0;background:#fff">
  <h2 style="margin:0 0 4px">{esc(f)}</h2>
  <div style="color:#555;margin-bottom:8px">
    博主 <b>{esc(r['blogger'])}</b> · 命中 <b style="color:#0a0">{n_hit}</b> 条 ·
    方向 {esc(m.get('direction'))} · 最新期 {esc(m.get('max_period'))} ·
    matched {esc(m.get('n_matched'))}/{esc(m.get('n_annotated'))}
  </div>
  <div style="color:#777;font-size:12px">A-G 校验: {esc(checks)}</div>
  <div style="color:#777;font-size:12px">源图: <code>{esc(src)}</code> ·
  裁剪图: <code>{esc(cd)}/02_annotated.png</code></div>
  <div style="display:flex;gap:16px;flex-wrap:wrap;margin-top:10px">
    <div>
      <img src="{uri}" style="max-height:640px;border:1px solid #ddd;border-radius:4px"
           alt="02_annotated.png"/>
    </div>
    <div style="flex:1;min-width:420px">
      <h3 style="margin:0 0 6px">匹配行(= 规律来源)</h3>
      <table style="border-collapse:collapse;font-size:13px">
        <tr><th style="border:1px solid #ddd;padding:2px 8px">行</th>
            <th style="border:1px solid #ddd;padding:2px 8px">期号</th>
            <th style="border:1px solid #ddd;padding:2px 8px">开奖号</th>
            <th style="border:1px solid #ddd;padding:2px 8px">匹配</th></tr>
        {rows}
      </table>
      <h3 style="margin:14px 0 6px">规律(✓=命中 26233)</h3>
      <table style="border-collapse:collapse;font-size:13px">
        <tr><th style="border:1px solid #ddd;padding:2px 8px">命中</th>
            <th style="border:1px solid #ddd;padding:2px 8px">类型</th>
            <th style="border:1px solid #ddd;padding:2px 8px">位置</th>
            <th style="border:1px solid #ddd;padding:2px 8px">数字</th>
            <th style="border:1px solid #ddd;padding:2px 8px">描述</th></tr>
        {pattern_rows(f)}
      </table>
    </div>
  </div>
</section>
"""


def main():
    body = "\n".join(
        f'<p style="margin:6px 0"><b>{esc(note)}</b></p>{row_html(f)}'
        for f, note in SELECT)
    page = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>26233 命中规律图集</title>
<style>
 body {{ font-family:-apple-system,"PingFang SC",sans-serif;max-width:1200px;margin:0 auto;padding:20px;background:#f7f7f7 }}
 table {{ border-collapse:collapse }} th,td {{ border:1px solid #ddd;padding:2px 8px;text-align:left }}
 .hit {{ background:#e6f6e6 }} .miss {{ color:#888 }}
 h2 {{ font-size:15px }} code {{ background:#eee;padding:1px 4px;border-radius:3px;font-size:12px }}
</style></head><body>
<h1>26233 期([1,6,3,4,0]) 命中规律图集</h1>
<p>目标期 26233=1 6 3 4 0 · 规律 2145 条 · 命中 454 条(21.2%, in-sample) /
  oos 16.5% · 命中图 190/195</p>
{body}
</body></html>"""
    out = os.path.join(REPO, "docs", f"命中规律图集-{DATE}.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)
    print("图集 ->", out, f"({os.path.getsize(out)//1024} KB)")


if __name__ == "__main__":
    main()
