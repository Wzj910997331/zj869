#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
逐图 GLM 视觉识别：读取博主走势图，输出 vision_patterns_full.json 结构
{file, type(走势图圈选/其他/文字预测截图/杀号表), period, patterns[{type,position,numbers,desc}]}

用法:
  python tools/recognize_patterns.py --base data/crawl/20260829 \
      --period 26231 --calib 26230 --calib-draw "9 4 6 8 3" \
      --workers 8 --out data/crawl/20260829/vision_patterns_full.json
  python tools/recognize_patterns.py --base data/crawl/20260830 \
      --period 26232 --calib 26231 --calib-draw "1 8 7 9 9" \
      --workers 8 --out data/crawl/20260830/vision_patterns_full.json
"""
import argparse
import base64
import concurrent.futures
import json
import os
import re
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_URL = "http://llm.riverbegin.cn/v1/chat/completions"
MODEL = "glm-5.3-flash"

# 原图白名单：s_2_<uuid>_<n>.<ext>。排除其他管线（image_recognize 等）在
# images 目录旁写的 *.direct.jpg / *.loc.jpg 等临时缩放图。
SOURCE_RE = re.compile(r"^s_2_[0-9a-f-]{36}_\d+\.(jpg|jpeg|png)$")


def load_api_key():
    k = os.environ.get("DEEPSEEK1_API_KEY")
    if k:
        return k
    for p in (os.path.join(REPO, ".credentials.yaml"),
              os.path.expanduser("~/.dsh/.credentials.yaml"),
              os.path.expanduser("~/.claude/.credentials.yaml")):
        if os.path.exists(p):
            try:
                import yaml
                d = yaml.safe_load(open(p, encoding="utf-8"))
                return d.get("DEEPSEEK1_API_KEY")
            except Exception:
                pass
    return None


def shrink_image(path, max_edge=512, quality=80):
    """缩小图：长边缩到 max_edge + JPEG q。裁剪图 PNG 中位 575KB → 缩后 32KB（~18x）。
    视觉模型耗时 ∝ 面积/体积，缩小后上传+推理+超时率都大降；风险是手画标注清晰度，
    需 A/B 验证后再全量启用。失败时回退原图 bytes。返回 (data_bytes, mime)。"""
    try:
        from PIL import Image
        import io
        with Image.open(path) as im:
            if im.mode in ("RGBA", "P", "LA"):
                im = im.convert("RGB")
            w, h = im.size
            if max(w, h) > max_edge:
                r = max_edge / max(w, h)
                im = im.resize((int(w * r), int(h * r)), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=quality)
            return buf.getvalue(), "image/jpeg"
    except Exception:
        with open(path, "rb") as f:
            return f.read(), ("image/png" if path.lower().endswith(".png") else "image/jpeg")


def call_vision(api_key, image_path, prompt, timeout=60, shrink=False):
    if shrink:
        data, mime = shrink_image(image_path)
    else:
        with open(image_path, "rb") as f:
            data = f.read()
        mime = "image/jpeg"
        if image_path.lower().endswith(".png"):
            mime = "image/png"
    b64 = base64.b64encode(data).decode()
    # 网关模型为"始终思考"型：隐藏 reasoning 会吃 token，max_tokens 必须给足（16000）
    # 否则 content 被截断为空 → empty。裁剪图 prompt 更复杂，2000 必空。
    body = {
        "model": MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64," + b64}},
            ],
        }],
        "temperature": 0.1,
        "max_tokens": 16000,
    }
    req = urllib.request.Request(
        API_URL, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
    return data["choices"][0]["message"]["content"]


def extract_json(text):
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    i, j = text.find("{"), text.rfind("}")
    if i >= 0 and j > i:
        try:
            return json.loads(text[i:j + 1])
        except Exception:
            pass
    return None


def image_area(path):
    """读取图片分辨率（仅解析头部，不整图解码），返回像素面积；失败则用文件大小兜底。"""
    try:
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
            return w * h
    except Exception:
        pass
    try:
        return os.path.getsize(path)
    except Exception:
        return 0  # 文件缺失等极端情况：面积 0 → 排到最前，one() 内再容错


def adaptive_timeout(path):
    """大图识别更慢（图片按面积排序，越后面越大），超时随面积递增。
    公式: max(60, area//60000)，封顶 180s。约 240万px→60s，400万→83s，1200万→180s。"""
    a = image_area(path)
    return min(180, max(60, a // 60000))


MAX_TIMEOUT = 300  # 单图超时封顶 5min：每次超时 +1min 递增，到顶仍超时就放弃该图


def _is_timeout(e):
    """识别超时异常（urllib timeout 在 py3.10 为 TimeoutError/socket.timeout）。"""
    if isinstance(e, TimeoutError):
        return True
    s = str(e).lower()
    return "timed out" in s or "timeout" in s or "timed_out" in s


def dedupe_merge(results, existing):
    """合并新识别结果与 resume 已存条目，按 file 去重（新结果覆盖旧 error 条目）。"""
    merged = {}
    for v in existing.values():
        merged[v["file"]] = v
    for v in results:
        merged[v["file"]] = v  # 新结果覆盖旧条目（error 重试成功后替换）
    return list(merged.values())


def build_prompt(filename, period, calib, calib_draw):
    return f"""读取此排列五走势图。上一期已开奖校准行{calib}={calib_draw}（万 千 百 十 个）。目标期{period}在最后一行。
请判断图片类型(走势图圈选/文字预测截图/杀号表/其他)。若是走势图圈选，列出博主画的所有预测标注：每条含type(定位/斜连/胆码/头/尾/和值/杀号)、position(万/千/百/十/个位)、numbers(数字列表)、desc(一句话，含连线期号与预测数字)。博主常画2-4个位置务必全列。
只回JSON:{{"type":"","period":"{period}","patterns":[{{"type":"","position":null,"numbers":[],"desc":""}}]}}"""


def build_crops_prompt(filename, period):
    """裁剪图（02_annotated.png 仅博主标注行栈）专用 prompt。
    裁剪图无完整走势图/期号列，只有博主画了规律标注的行 + 红色 row 标签，
    因此去掉"目标期在最后一行"的整图锚定，改为直接识别博主预测标注。"""
    return f"""你是排列五走势图分析专家。这张图是博主在某期排列五走势图上手画的预测标注（已裁剪为标注行栈，每行左侧红色 row 标签为行号；数字区从左到右为 万/千/百/十/个 位）。
博主预测的目标期是 {period}。
请判断图片类型(走势图圈选/文字预测截图/杀号表/其他)。若是走势图圈选，列出博主画的所有预测标注：每条含type(定位/斜连/胆码/头/尾/和值/杀号)、position(万/千/百/十/个位)、numbers(数字列表)、desc(一句话，含标注方式与预测数字)。博主常画2-4个位置务必全列。
只回JSON:{{"type":"","period":"{period}","patterns":[{{"type":"","position":null,"numbers":[],"desc":""}}]}}"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="data/crawl/YYYYMMDD 目录（含 images/）")
    ap.add_argument("--period", required=True, help="预测期号，如 26231")
    ap.add_argument("--calib", required=True, help="校准期号，如 26230")
    ap.add_argument("--calib-draw", required=True, help="校准期开奖，如 '9 4 6 8 3'")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", required=True, help="输出 vision_patterns_full.json")
    ap.add_argument("--crops-dir", default=None,
                    help="裁剪产物根目录（如 data/recognize/20260830_all）。给定则读 crops_all_manifest.json 中 "
                         "status==cropped 的图、用 02_annotated.png 识别；否则读 images/ 目录整图。")
    ap.add_argument("--limit", type=int, default=0, help="只识别前 N 张（调试）")
    ap.add_argument("--offset", type=int, default=0, help="从排序后的第 N 张开始（配合 --limit 分批）")
    ap.add_argument("--resume", action="store_true", help="已存在输出时跳过已完成图片")
    ap.add_argument("--shrink", action="store_true",
                    help="识别前把图缩小（长边512+JPEG q80，~18x 体积压缩）。提速但手画标注清晰度降，需 A/B 验证")
    args = ap.parse_args()

    api_key = load_api_key()
    if not api_key:
        print("找不到 DEEPSEEK1_API_KEY")
        sys.exit(1)

    crops_manifest = None
    if args.crops_dir:
        # 裁剪模式：读 crops_all_manifest.json，只取 status==cropped 的图
        mp = os.path.join(args.crops_dir, "crops_all_manifest.json")
        if not os.path.exists(mp):
            print(f"找不到裁剪 manifest: {mp}")
            sys.exit(2)
        crops_manifest = json.load(open(mp, encoding="utf-8"))
        files = [r["file"] for r in crops_manifest["images"].values()
                 if r.get("status") == "cropped"]
        img_dir = args.crops_dir  # one() 里据此拼 crop_dir/02_annotated.png
        print(f"裁剪模式：{len(files)} 张 cropped 图（excluded {crops_manifest['n_total'] - len(files)} 张跳过）")
    else:
        img_dir = os.path.join(args.base, "images")
        files = [f for f in os.listdir(img_dir) if SOURCE_RE.match(f)]  # 白名单：仅原图

    def _img_path(f):
        """返回该图实际识别输入路径（裁剪模式=裁剪图；整图模式=原图）。"""
        if crops_manifest:
            rec = crops_manifest["images"].get(f)
            return os.path.join(args.crops_dir, rec["crop_dir"], "02_annotated.png")
        return os.path.join(img_dir, f)

    files.sort(key=lambda f: image_area(_img_path(f)))  # 分辨率小→大，小图先识别
    if args.offset:
        files = files[args.offset:]
    if args.limit:
        files = files[:args.limit]
    print(f"识别 {len(files)} 张图 @ {MODEL} workers={args.workers} offset={args.offset}")

    existing = {}
    if args.resume and os.path.exists(args.out):
        try:
            for v in json.load(open(args.out, encoding="utf-8")):
                existing[v["file"]] = v
            print(f"resume: 已有 {len(existing)} 条")
        except Exception as e:
            # 上次运行被 kill 可能留下半截 JSON：从头识别（此时 --resume 退化为全量）
            print(f"警告: 读取 {args.out} 失败（{e}），损坏文件将从头识别")
            existing = {}
    # error 条目不算已完成：重新识别（裁剪图模式下 26231/26232 的 timed out/empty 都重试）
    todo = [f for f in files if f not in existing or existing[f].get("error")]

    def one(f):
        img = _img_path(f)
        to = adaptive_timeout(img)  # 面积自适应基础超时（大图更慢）
        if args.shrink:
            # A/B 实测：缩小图请求轻但网关生成时间不随面积线性降，
            # 90s timeout 在网关慢窗口失败率 ~50%，保底抬到 120s。
            to = min(180, max(120, to))
        for attempt in range(1, 6):  # 最多 5 次尝试（超时逐级 +1min，5min 封顶）
            try:
                prompt = (build_crops_prompt(f, args.period) if crops_manifest
                          else build_prompt(f, args.period, args.calib, args.calib_draw))
                raw = call_vision(api_key, img, prompt, timeout=to, shrink=args.shrink)
                v = extract_json(raw)
                if v is None:
                    if attempt >= 3:
                        return {"file": f, "type": "其他", "period": args.period, "patterns": [], "error": "empty"}
                    time.sleep(2 + attempt)
                    continue
                if "file" not in v:
                    v["file"] = f
                if "type" not in v:
                    v["type"] = "其他"
                if "patterns" not in v:
                    v["patterns"] = []
                return v
            except urllib.error.HTTPError as e:
                time.sleep(5 * attempt)
                if attempt >= 3:
                    return {"file": f, "type": "其他", "period": args.period, "patterns": [], "error": f"HTTP {e.code}"}
            except Exception as e:
                if _is_timeout(e) and to < MAX_TIMEOUT:
                    to = min(MAX_TIMEOUT, to + 60)  # 超时一次 +1min，最大 5min
                    time.sleep(3)  # 慢窗口退避
                    continue
                time.sleep(3 * attempt)
                if attempt >= 3:
                    return {"file": f, "type": "其他", "period": args.period, "patterns": [], "error": str(e)[:80]}
        return {"file": f, "type": "其他", "period": args.period, "patterns": [], "error": f"timeout>={MAX_TIMEOUT}s"}

    results = []
    done_n = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(one, f): f for f in todo}
        for i, fut in enumerate(concurrent.futures.as_completed(futs), 1):
            r = fut.result()
            results.append(r)
            done_n += 1
            if i % 20 == 0:
                print(f"  进度 {i}/{len(todo)}")
            if r.get("error"):
                print(f"  [err] {r['file']}: {r['error']}")
            # 实时落盘：每 3 张写一次，便于外部监控进度；批超时被杀时不丢已识别部分
            if i % 3 == 0:
                merged_now = dedupe_merge(results, existing)
                # 原子写：先写 tmp 再 rename，避免被 kill 时留下半截 JSON（resume 会读到损坏文件）
                tmp = args.out + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(merged_now, f, ensure_ascii=False, indent=1)
                os.replace(tmp, args.out)
                with open(args.out + ".progress", "w", encoding="utf-8") as f:
                    f.write(str(len(merged_now)))
                print(f"  saved {len(merged_now)}/{len(files)} -> {args.out}")

    merged = dedupe_merge(results, existing)
    tmp = args.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=1)
    os.replace(tmp, args.out)
    from collections import Counter
    print("=" * 40)
    print(f"完成 {len(merged)} 条 -> {args.out}")
    print("类型分布:", dict(Counter(v.get("type") for v in merged)))


if __name__ == "__main__":
    main()
