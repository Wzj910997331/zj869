#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""read_guihua.py — 读博主「画规」(画法/连线逻辑) + 交叉验证。

背景(2026-09-02 修正):旧单押管线只做了「位置锚定」(校准行 26230=94683 反推列位),
read_blogger_prediction.py 的 prompt 只让模型输出 {位置,候选},**从不读博主怎么画**,
导致 docs/规律/26231.md 的「博主画规逻辑」列是通用兜底串("博主在目标期行手写万位=1"),
不是真画规。本模块补上这一步:读**整张命中原图**,让视觉模型描述博主画的线/圈/连法,
并做交叉验证(结构化 prompt ×2 复现 + 叙事 prompt + 人工读对账)。

输入:命中原图(整张走势图,画规跨多行,不能用只裁目标行的窄条)。
输出:data/crawl/<date>/guihua_26231_reads.json
  {period, draw, calib, calib_draw, images:{file:{blogger, hits, reads:[
    {variant, model, 画规类型, 画笔元素[], 画法描述, 推导逻辑, 预测[], raw}], consensus:{...}}}}

用法:
  python3 modules/image_recognize/read_guihua.py \
      --period 26231 --draw "1 8 7 9 9" --calib 26230 --calib-draw "9 4 6 8 3" \
      --hits data/crawl/20260829/评审_26231命中5/hits.json \
      --images data/crawl/20260829/评审_26231命中5/images \
      --out data/crawl/20260829/guihua_26231_reads.json --per 2 --workers 6
"""
import argparse
import json
import os
import sys
import base64
import io
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_IMG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _IMG)

from PIL import Image  # noqa: E402
from stage4_llm import call_llm  # noqa: E402

GLM_MODEL = "glm-5.3-flash"


def read_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def load_b64(path, max_side=780):
    """读整张原图,按最长边缩到 max_side(保留细节又不超 GLM 视野),返回 base64 PNG。"""
    im = Image.open(path).convert("RGB")
    w, h = im.size
    m = max(w, h)
    if m > max_side:
        sc = max_side / m
        im = im.resize((int(w * sc), int(h * sc)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


PROMPT_STRUCT = (
    "这是一张排列五走势图,最左通常有期号列和一条和值/辅助列,可忽略;真正的是「万 千 百 十 个」5 列开奖数字。\n"
    "{calib_txt}\n"
    "博主在图上用彩标(圈选/划线/连线/箭头/斜连/框选)画了一套**画规规律**。请你把博主**画的规律本身**完整描述出来,"
    "只输出一个 JSON 对象,格式:\n"
    "{{\n"
    "  \"画规类型\": \"斜连|直连|重号|邻号|遗漏|冷热|对称|框选胆码|定位|对调交叉 (一拍或多拍,逗号隔开)|无规律可推(博主疑乱画/巧合)\",\n"
    "  \"画笔元素\": [{{\"元素\":\"圈选|连线|箭头|框选|斜连\",\"位置\":\"万位\",\"数字\":9,\"期号\":\"26230\"}}],\n"
    "  \"画法描述\": \"一句话:博主从哪一期哪一位的哪个数,沿什么路线连/圈到哪\",\n"
    "  \"推导逻辑\": \"博主按此规律如何推出对本期({period})的预测\",\n"
    "  \"预测\": [{{\"位置\":\"万\",\"数字\":1}}]\n"
    "}}\n"
    "规则(必须严格遵守,违反即判错):\n"
    "- 只读博主**画在这张图上**的标注(圈/线/箭头/斜连),不要自己编规律,不要给博主没画的数字编位置。\n"
    "- 规律只能从**博主画在这张图的线条轨迹本身**推导;严禁为凑规律而引入**其它期、其它列**的历史先例"
    "(如\"万位某组合先前在别的期出现过、跟过某数,故这次再出就该跟某数\")——博主没画的=不存在,那是我要防的幻觉。\n"
    "- **逐图隔离**:只依据这一张图上的博主标注。其它期的开奖值只作**链条锚定**(博主确实用线连到了它们/或用于校准列位),"
    "不作\"先例\"式规律依据。\n"
    "- **根据博主画的轨迹线推理规律**:重点找博主**亲手用彩标连/圈出来的那条曲线轨迹**(圈选连到哪些数、箭头/斜连从哪到哪)。"
    "规律必须是这条**轨迹线本身**的走向,不是读图后你脑补的走势。\n"
    "- **识别签名串重复(重要,最容易漏)**:若博主把同一串数字**画了两次**——无论在同一列的不同期段(如某位 `2,9,1` 圈了两遍 → "
    "\"2,9 之后出 1\"),还是两列同型(如千位 `1,4,2→1`、万位 `1,4,2→1` 各画一遍)——这就是博主画出的**签名重复**规律:"
    "前一次该签名后接 X,后一次(当前)该签名再现即预测 X。一定要数清博主**实际画了哪几个数/哪几列**,把签名串**读完整**,"
    "别只看末尾一两个就下结论。\n"
    "- **诚实至上**:若博主画的线条**推不出**对 {period} 的任何预测(博主疑乱画/随手押一位/线条不闭环),"
    "画规类型填\"无规律可推(博主疑乱画/巧合)\",推导逻辑填\"无逻辑可推\",预测给博主明确写出的数字即可;"
    "**严禁**为了凑出一个\"规律\"而强行硬套前面出现过的走势。\n"
    "- 预测=博主靠这套画法对 {period} 期给出的各位置数字(可能多位置);若博主某位圈了但不止一个数就列多个。\n"
    "- 画规类型填你从图上看到的真实画法;若确无规律可推,如实填\"无规律可推\",不要硬造。\n"
    "**不要思考、不要推理、不要解释**:直接读图填上面 JSON,只输出一个 JSON 对象,其余一律不写。"
)

PROMPT_NARR = (
    "这是一张排列五走势图。上一期 {calib_period} 开奖 = {calib_draw}(万 千 百 十 个)。本期 {period} 开奖 = {draw}。\n"
    "博主在图上画了彩标(圈/线/箭头/斜连/框选)。请用 2~4 句中文,仔细描述博主画的**整体画规规律**:"
    "博主从哪个数的哪一位出发、沿什么路线连到哪几个数、圈了哪几个数、最后怎么据此推出对本期({period})的预测(各位置数字)。\n"
    "只输出这段描述文字,不要输出 JSON、不要解释其它、不要加标题。"
)


def extract_json_obj(text):
    """从模型输出里抽第一个 {..} JSON 对象(兼容 ```json 围栏/散文包裹)。"""
    if not text:
        return None
    s = text
    if "```" in s:
        # 取围栏里最大段
        segs = s.split("```")
        for seg in segs:
            if "{" in seg and "}" in seg:
                s = seg
                break
    i = s.find("{")
    j = s.rfind("}")
    if i < 0 or j < i:
        return None
    try:
        return json.loads(s[i:j + 1])
    except Exception:
        return None


def one_read(image_path, prompt, model=GLM_MODEL, max_tokens=16000):
    """读一张原图画规,返回 (parsed_json, raw_text)。"""
    b64 = load_b64(image_path)
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]}]
    try:
        raw = call_llm(model, msgs, max_tokens=max_tokens, timeout=240)
    except Exception as e:
        return None, f"__ERROR__ {e}"
    return extract_json_obj(raw), raw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", required=True)
    ap.add_argument("--draw", required=True)
    ap.add_argument("--calib", required=True)
    ap.add_argument("--calib-draw", required=True)
    ap.add_argument("--hits", required=True, help="hits.json 或 docs/规律/<period>.json(含 rules)")
    ap.add_argument("--images", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--per", type=int, default=2, help="每个 variant 重复次数(复现性)")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--model", default=GLM_MODEL)
    args = ap.parse_args()

    calib_txt = (f"上一期 {args.calib} 开奖 = {args.calib_draw}(万=第1个 千=第2 百=第3 十=第4 个=第5);"
                 f"请先找到这行,用它确认「哪个格是万位」,再读博主标注。")
    hits = read_json(args.hits)
    raw_hits = hits.get("hits") or hits.get("rules") or []
    # 命中集: file + blogger + 位置 + 候选(命中位)
    images = {}
    for h in raw_hits:
        f = h["file"]
        if f not in images:
            images[f] = {"file": f, "blogger": h.get("blogger", ""), "hits": []}
        images[f]["hits"].append({"位置": h.get("位置") or h.get("hit_position"),
                                  "候选": h.get("候选") or h.get("hit_numbers")})

    p_struct = PROMPT_STRUCT.format(calib_txt=calib_txt, period=args.period)
    p_narr = PROMPT_NARR.format(calib_period=args.calib, calib_draw=args.calib_draw,
                                period=args.period, draw=args.draw)

    tasks = []  # (file, image_path, variant, label)
    for f in images:
        for r in range(args.per):
            tasks.append((f, os.path.join(args.images, f), "struct", f"struct#{r + 1}"))
            tasks.append((f, os.path.join(args.images, f), "narr", f"narr#{r + 1}"))

    results = {f: {"reads": []} for f in images}
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {}
        for f, ipath, variant, vt in tasks:
            prompt = p_struct if variant == "struct" else p_narr
            futs[ex.submit(one_read, ipath, prompt, args.model)] = (f, variant, vt, ipath)
        for fu in as_completed(futs):
            f, variant, vt, ipath = futs[fu]
            parsed, raw = fu.result()
            it = {"variant": variant, "run": vt, "model": args.model,
                  "ok": parsed is not None or (isinstance(raw, str) and not raw.startswith("__ERROR__"))}
            if parsed is not None:
                it["画规类型"] = parsed.get("画规类型", "")
                it["画笔元素"] = parsed.get("画笔元素", [])
                it["画法描述"] = parsed.get("画法描述", "")
                it["推导逻辑"] = parsed.get("推导逻辑", "")
                it["预测"] = parsed.get("预测", [])
            else:
                it["画法描述"] = (raw or "")[:300]
            results[f]["reads"].append(it)
            results[f]["blogger"] = images[f]["blogger"]
            results[f]["file"] = f

    out = {"period": args.period, "draw": args.draw,
           "calib": args.calib, "calib_draw": args.calib_draw,
           "说明": "逐张命中原图画规读取(整张原图),结构化×%d+叙事×%d 交叉验证,需与人工读对账"% (args.per, args.per),
           "images": results}
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)

    n_ok = sum(1 for f in results.values() for r in f["reads"] if r["ok"])
    n_tot = sum(len(f["reads"]) for f in results.values())
    print(f"画规读取完成: {n_ok}/{n_tot} 次读取成功 ({len(images)} 张图 × {args.per*2} 次)")
    for f, d in results.items():
        print(f"  {d['blogger']:10s} {f[:40]} : " +
              "; ".join(f"[{r['variant']}:{'ok' if r['ok'] else 'FAIL'}]" for r in d["reads"]))
    print(f"→ {args.out}")


if __name__ == "__main__":
    main()
