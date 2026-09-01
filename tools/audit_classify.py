#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM 分类复核审计：对抽样图片用中性 prompt 独立判定内容，对比当前 json 的分类。"""
import base64, json, os, sys, urllib.request
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
API_URL = "http://llm.riverbegin.cn/v1/chat/completions"
MODEL = "glm-5.3-flash"
REPO = "/data/zhenjie/zj869"

def load_api_key():
    for p in (os.path.join(REPO, ".credentials.yaml"), os.path.expanduser("~/.dsh/.credentials.yaml")):
        if os.path.exists(p):
            import yaml
            return yaml.safe_load(open(p, encoding="utf-8")).get("DEEPSEEK1_API_KEY")
    return os.environ.get("DEEPSEEK1_API_KEY")

def call(img):
    with open(img, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    prompt = "请用一句话客观描述这张图片的实际内容：它是排列五走势图（历史开奖数字表格）且博主在上面画了圈/线/框等标注吗？还是纯文字预测截图、数字缩水表、杀号表？只回答类别和简短依据。"
    body = {"model": MODEL, "messages": [{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}}]}],
        "temperature": 0.1, "max_tokens": 300}
    req = urllib.request.Request(API_URL, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())["choices"][0]["message"]["content"]

# 抽样清单
samples = []
cur = json.load(open(os.path.join(REPO, "data/crawl/20260829/vision_patterns_full.json"), encoding="utf-8"))
def pick(cls, n, base="20260829"):
    imgdir = os.path.join(REPO, f"data/crawl/{base}/images")
    got = []
    for v in cur if base == "20260829" else old:
        if len(got) >= n: break
        if v.get("type") == cls and os.path.exists(os.path.join(imgdir, v["file"])):
            got.append(v["file"])
    return got
for cls in ["文字预测截图", "其他", "走势图圈选"]:
    for f in pick(cls, 3):
        samples.append((cls, "20260829", f))
old = json.load(open(os.path.join(REPO, "data/crawl/20260828/vision_patterns_full.json"), encoding="utf-8"))
for v in old[:20]:
    if v.get("type") == "走势图圈选" and os.path.exists(os.path.join(REPO, "data/crawl/20260828/images", v["file"])):
        samples.append(("走势图圈选(对照)", "20260828", v["file"]))
        if sum(1 for s in samples if s[1] == "20260828") >= 2: break

print(f"复核 {len(samples)} 张...")
api_key = load_api_key()
for i, (orig, base, f) in enumerate(samples, 1):
    img = os.path.join(REPO, f"data/crawl/{base}/images", f)
    try:
        txt = call(img)
        print(f"[{i}/{len(samples)}] 原分类={orig}\n   GLM复核={txt[:120]}")
    except Exception as e:
        print(f"[{i}] {f} 失败: {e}")
