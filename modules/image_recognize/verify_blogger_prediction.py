#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_blogger_prediction.py — 单押命中判定（纯算术，零视觉）。

吃 read_blogger_prediction 的 blogger_predictions.json（含 predicted_positions），
逐位置做确定性命中判定。命中基准（用户 2026-09-02 口径）：博主一位**只写一个数字**
且该位实开该数才计命中。不再把「程序自摸历史规律」当命中。

分类（每条预测位置）：
  hit        单押 1 码 + 位对 + 数字对（博主预测=实开），且位名已校准（calib-anchor / 显式 override）
  unverified 单押 1 码 + 数字对，但位名只是 glm-read（未校准）→ **候选命中**：须校准行锚定/人工复核才计
  miss       单押 1 码但位没对/数字没对上（单码错位/未中）
  excluded   双选(≥2 码 宽网)/不定位(无位置)/报号(图级 reject_reason)/缺图/API 失败

命中纪律（2026-09-03 审计定案，防列位偏移假命中）：博主目标期行**画满 5 位/多位**时，
GLM 自报位名会整串右/左移（漏期号/和值列→万千百十个错位）→ 产生假命中（26233 实锤 2 条：
生活很无奈帖b 23169 被读成右移 4 位取百3、情有独钟0 十0 被错配个0）。
**根治**：命中只认「位名已被校准」的位置（calib-anchor=校准行锚定 / 逐列受控读 / 人工看图复核）；
glm-read 的候选命中一律记 `unverified`，默认**不计入命中**。`--trust-glm-read` 显式恢复旧行为（默认关）。

读源字段：blogger_predictions.json 每条 {file,blogger,predicted_positions,reject_reason,
          multi,logic,position_check,error,target_period}；predicted_positions[i]={位置,候选,标注方式,原文}。

输出 data/crawl/<date>/blogger_predictions_verify.json:
  {period,draw,说明,images:{file:{...}},统计:{采集,命中,剔除}}。

用法:
  python3 modules/image_recognize/verify_blogger_prediction.py --date 20260829 \
      --period 26231 --draw "1 8 7 9 9" \
      --pred data/crawl/20260829/blogger_predictions.json \
      --out data/crawl/20260829/blogger_predictions_verify.json
"""
import argparse
import json
import os
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _REPO)

from modules.image_recognize.common import (POS_NAMES, parse_position)  # noqa: E402

# 命中纪律：位置名默认「裸 glm-read = 未校准」。凡经 --position-overrides 显式改名的位置，
# pos_source 会变成 calib-anchor / glm-read-plus-drawtbl / user-anchor 等 → 视为已锚定、可计命中。
# 判定就一条：pos_source != "glm-read" 即可信（覆盖 26231 glm-read-plus-drawtbl 等既有来源名）。


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def norm_candidates(cand):
    """候选数字归一化为 int 列表；非数字剔除。"""
    out = []
    for c in cand or []:
        try:
            out.append(int(str(c).strip()))
        except (ValueError, TypeError):
            continue
    return out


def classify_positions(rec, draw, overrides=None, trust_glm_read=False):
    """对一条预测记录展开逐位置分类。返回 (records, image_excluded, image_reason)。

    位置来源（绝不盲信 GLM 读数里自带的位置名，那是循环校验——模型说"百位"就查百位）：
      position_source:
        calib-anchor  位置名经校准行(前一期已知开奖如 26230=9 4 6 8 3)锚定/校正（确定性，模型无关）
        glm-read      未校准，沿用 GLM 读出的位置名（风险：期号/和值列污染导致偏移）

    overrides: {file: {glm_name: ({name, source} | str)}}
      由已验证的校准行锚定结果给出：把 GLM 读错的位置名改成校准行确认的位置名。
      例 富老师_2：GLM 读"百位"实为"千位" → {'百位': {'name':'千位','source':'calib-anchor'}}。

    records: [{位置,候选,pnums,pos,actual,cls,单押,标注方式,原文,position_source}, ...]
      其中 cls ∈ hit/unverified/miss/wide/noloc；img_excluded 时会话外。

    trust_glm_read=True 时恢复旧行为（glm-read 位名命中直接计 hit）。
    """
    reject = rec.get("reject_reason")
    if rec.get("error"):
        return [], True, f"读图/api 失败：{rec['error']}"
    if reject:
        return [], True, f"报号/无画规：{reject}"

    preds = rec.get("predicted_positions") or []
    if not preds:
        return [], True, "未读出博主目标期行预测（无 predicted_positions）"

    file = rec.get("file")
    file_ovr = (overrides or {}).get(file, {})

    records = []
    hit = 0
    for p in preds:
        posname = p.get("位置", "")
        pos_source = "glm-read"
        ovr = file_ovr.get(posname)
        if isinstance(ovr, dict):
            posname = ovr.get("name", posname)
            pos_source = ovr.get("source", "calib-anchor")
        elif isinstance(ovr, str):
            posname = ovr
            pos_source = "calib-anchor"
        pos = parse_position(posname)
        pnums = norm_candidates(p.get("候选"))
        actual = draw[pos] if (pos is not None and 0 <= pos < len(draw)) else None
        if pos is None or actual is None:
            cls = "noloc"        # 不定位/无法对位
            single = False
        elif len(pnums) > 1:
            cls = "wide"         # 多码宽网
            single = False
        elif len(pnums) == 1:
            if actual == pnums[0]:
                # 命中纪律：数字对只算“候选命中”，位名须已锚定（pos_source≠裸 glm-read，
                # 即经校准行锚定/交叉验证/逐列读/人工复核）或 --trust-glm-read 才计真命中；
                # 否则记 unverified（候选命中，待复核）——防列位偏移假命中。
                if trust_glm_read or pos_source != "glm-read":
                    cls = "hit"
                    hit += 1
                else:
                    cls = "unverified"
                single = True
            else:
                cls = "miss"     # 单码错位/未中
                single = True
        else:
            cls = "wide"         # 候选为空 → 无法推出唯一
            single = False
        records.append({"位置": posname, "候选": p.get("候选") or [], "pnums": pnums,
                        "pos": pos, "实际": actual, "cls": cls, "单押": single,
                        "标注方式": p.get("标注方式", ""), "原文": p.get("原文", ""),
                        "position_source": pos_source})
    return records, False, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--period", required=True)
    ap.add_argument("--draw", required=True)
    ap.add_argument("--pred", required=True, help="blogger_predictions.json")
    ap.add_argument("--position-overrides", default=None,
                    help="校准行锚定的位置覆盖 JSON：{file:{glm位置名:{name,source}|str}}——"
                         "把 GLM 读错的位置名改成校准行(如 26230=9 4 6 8 3)确认的位置名")
    ap.add_argument("--out", default=None, help="输出 blogger_predictions_verify.json")
    ap.add_argument("--trust-glm-read", action="store_true",
                    help="信任 GLM 自报位名的命中（旧行为，默认关）。命中纪律 2026-09-03：博主画满 5 位/多位时"
                         "glm-read 位名会整串列位偏移 → 假命中（26233 帖b/情有独钟0 实锤）；默认 glm-read 命中记 "
                         "unverified（候选），须 --position-overrides 锚定或人工/二次识读复核才计 hit")
    args = ap.parse_args()

    draw = [int(x) for x in args.draw.split()]
    src = read_json(args.pred)
    preds = src.get("predictions", [])
    overrides = None
    if args.position_overrides and os.path.exists(args.position_overrides):
        overrides = read_json(args.position_overrides)

    images = {}
    n_hit = n_collect = n_single = n_excluded = 0
    n_unv_img = n_unv_pos = 0
    excluded_by = Counter()
    trust = args.trust_glm_read

    for rec in preds:
        file = rec["file"]
        records, img_excl, reason = classify_positions(rec, draw, overrides, trust_glm_read=trust)
        has_hit = any(r["cls"] == "hit" for r in records)
        has_unv = any(r["cls"] == "unverified" for r in records)
        if img_excl:
            images[file] = {"file": file, "blogger": rec.get("blogger"),
                            "status": "excluded", "reason": reason,
                            "records": records}
            n_excluded += 1
            if reason:
                excluded_by["图级:" + reason[:20]] += 1
            continue
        # 采集 = 该图预测位置条数（单码+宽网+错位，博主画了规的预测尝试）
        n_collect += len(records)
        # 单码采集 = 博主「一位只写 1 个数」且能定位的预测位置（hit+miss+unverified）。
        #   B 空读(无数字)、C 多码/和值(wide)、不定位(noloc) 都排除 —— 它们不是单押，
        #   单押命中率应只以单码采集为基准。B 保留走视觉读成空，从不计入分母。
        n_single += sum(1 for r in records if r["cls"] in ("hit", "miss", "unverified"))
        images[file] = {"file": file, "blogger": rec.get("blogger"),
                        "status": "hit" if has_hit else ("unverified-hit" if has_unv else "prediction"),
                        "multi": rec.get("multi", ""),
                        "logic": rec.get("logic", ""),
                        "position_check": rec.get("position_check", {}),
                        "reject_reason": rec.get("reject_reason"),
                        "records": records}
        if has_hit:
            n_hit += 1
        if has_unv:
            n_unv_img += 1
        for r in records:
            if r["cls"] == "unverified":
                n_unv_pos += 1
            elif r["cls"] == "wide":
                excluded_by["多码宽网(≥2码/和值)"] += 1
            elif r["cls"] == "miss":
                excluded_by["单码错位/未中"] += 1
            elif r["cls"] == "noloc":
                excluded_by["不定位"] += 1

    stats = {
        "period": args.period, "draw": args.draw,
        "送视觉图": len(preds),
        "命中图": n_hit,
        "候选命中图(待复核)": n_unv_img,
        "候选命中位置(待复核)": n_unv_pos,
        "预测位置采集": n_collect,
        "单码采集": n_single,
        "剔除图": n_excluded,
        "剔分类": dict(excluded_by),
    }
    out = {"period": args.period, "draw": args.draw,
           "说明": f"单押命中判定（{args.period}，博主目标期行手写，纯算术；trust_glm_read={trust}）",
           "分类口径": {
               "hit": "单押1码+位对+数字对，且位名已锚定（pos_source≠glm-read：校准行锚定/交叉验证/逐列读/人工复核）",
               "unverified": "单押1码+数字对，但位名只是 glm-read（未校准）→ 候选命中，默认不计命中，待复核",
               "miss": "单押1码但位没对/数字没对上",
               "excluded": "多码宽网(≥2码/和值)/不定位/报号(图级 reject_reason)/缺图/API失败",
               "position_source": "位置名来源（绝不盲信 GLM 自报位置名——那是循环校验）："
                                  "calib-anchor=经校准行(前一期已知开奖)锚定/校正，确定性命中；"
                                  "glm-read=沿用 GLM 位置名，未校准（默认不构成 hit）",
               "命中纪律": "2026-09-03 定案：博主画满 5 位/多位时 glm-read 位名整串偏移会假命中"
                          "（26233 帖b 23169→右移4位取百3、情有独钟0 十0→错配个0），故 glm-read 命中记 unverified；"
                          "--trust-glm-read 恢复旧行为",
               "单码采集": "单押1码且能定位 的预测位置(hit+miss+unverified)；多码/和值(C)、不定位、空(B) 不计入分母"},
           "统计": stats,
           "images": images}
    if args.out:
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        # 明细打印
        n_hit_rec = sum(1 for img in images.values()
                        for r in img.get("records", []) if r["cls"] == "hit")
        n_unv_rec = sum(1 for img in images.values()
                        for r in img.get("records", []) if r["cls"] == "unverified")
        print(f"送视觉 {len(preds)} 图 / 预测位置采集 {n_collect} 条 / 单码采集 {n_single} 条")
        print(f"  命中图 {n_hit}（锚定对位位置 {n_hit_rec}）｜ 候选命中(待复核) 图 {n_unv_img} / 位置 {n_unv_rec}")
        print(f"  剔除图 {n_excluded}：{dict(excluded_by)}")
        print(f"  → {args.out}")


if __name__ == "__main__":
    main()
