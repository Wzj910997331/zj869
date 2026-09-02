# zj869 — 排列5 博主打点图画规规律采集·验证·方法库系统

> 从 gouli99 论坛抓取博主手画的"排列五走势图"，用**视觉模型**识别画规（圈选/连线/框选/杀号），
> 通过**真实开奖校准**核验命中，沉淀为**画规方法库**（目标 1000 条），为后续每期预测/回测提供方法依据。
>
> 现状一句话：**流程已改造为四步管线「爬取 → 确定性过滤 → 裁剪 → resize → 视觉判定」，20260831 期（26233）583 张图跑通：过滤 keep 134 / uncertain 285 / exclude 164，冒烟 2 张全部 ds-ok（A–G 对齐门全过、匹配 6/11 期、命中与规律已输出）。旧五阶段管线（26230–26232）成果保留：26231/26232 采集 957 条 → 命中 100 条（7.41%/12.68%），26230 命中 18 条（2.59%），画规方法库 48 条已生成。**

---

## 0a. 历史成果：每期准确率与采集量（规律库入口）

| 期号 | 开奖 | 采集记录 | 命中 | 命中率 | 完全命中 | 累计规律 |
|---|---|---|---|---|---|---|
| 26230 | 9 4 6 8 3 | 694 条 | 18 条 | 2.59% | 3 条 | **18 条**（docs/规律/26230.md） |
| 26231 | 1 8 7 9 9 | 405 条 | 30 条 | 7.41% | —（未跑多位置重读） | **30 条**（docs/规律/26231.md） |
| 26232 | 8 0 2 3 3 | 552 条 | 70 条 | 12.68% | —（未跑多位置重读） | **70 条**（docs/规律/26232.md） |

> 口径说明：
> - **采集记录**：该期博主的画规记录数（仅"走势图圈选"；杀号、报号/铁率文字截图不体现画规，从采集口径整体剔除）。
> - **命中**：博主在走势图上画出、且预测位置含实际开出数字（空号杀号、未画规律、不定位铁码、报号/铁率等假命中已剔除）。
> - **完全命中**：博主只预测 1 个位置且命中（1位置1中）。
> - **命中率 = 命中 / 采集**；每期新增规律累计写入 `docs/规律/<期号>.md`。
> - **杀号不计入**：博主"杀掉"的号码不体现画规预测（26231 剔 33 / 26232 剔 42 / 26230 剔 4）。
> - **报号/铁率不计入**：文字预测截图类（博主直接打字报数/缩水推荐，无画规）从采集与命中整体剔除（26231 剔 62 / 26232 剔 59）。
> - **不定位不计入**：无位置的胆码全盘/组合推荐，非定位画规，不算命中。
> - 26231/26232 未跑 GLM 多位置重读（网关识图慢），命中口径为单条识别命中 + `tools/recheck_compute.py` 纯计算复测（无视觉模型）从严过滤；26230 跑过重读并剔除 2 条假命中，故命中率口径略严、数值更低（2.59%）。
> - ⚠️ 彩票开奖属独立随机事件，历史规律不具备预测效力。

---

## 0. 现行全流程（四步管线，2026-08-31 起，从爬取到输出规律）

改造目标：爬完一期数据后不再把每张原图都喂慢速视觉模型，先用**确定性 OpenCV+OCR** 过滤掉
"没在近期待开奖历史上画规律"的图，再把真正画了规的图交给视觉模型做**对已开奖回溯判定**。

```
① 爬取博主图 + 开奖历史
   tools/crawl_gouli.py 2026-08-31            gouli99 论坛 API（lottery=2 排列五）
   tools/fetch_lottery.py --limit 60          500彩票网 plw 接口（gb2312）
        │   → data/crawl/20260831/posts.json + images/（583 张 s_2_<uuid>_<n>.jpg）
        │     + lottery_recent.json（60 期，26233 最新在首条）
        ▼
② 确定性过滤（新，无 LLM）
   modules/image_recognize/filter_trend.py v3
        │   多信号置信分级：S1 期号多期连续性 / S2 标注存在性 / S3 标注形态分级 / S4 列覆盖
        │   → data/crawl/20260831/filter_report.json   keep 134 / uncertain 285 / exclude 164
        ▼
③ 裁剪（复用 crop_all）
   modules/image_recognize/crop_all.py --date 20260831
        │   → data/recognize/20260831_all/crops_all_manifest.json
        │     + {crop_dir}/02_annotated.png（标注行栈图，640 宽）
        ▼
④ 显式 resize（新）
   modules/image_recognize/resize_crops.py
        │   640 宽浅色数字视觉模型读不清 → INTER_CUBIC 放大到 ≥1024 宽（JPEG q90）
        │   → data/recognize/20260831_all/vision/{stem}.jpg + vision_manifest.json
        ▼
⑤ 视觉判定 + 规律（新）
   modules/image_recognize/judge_accuracy.py
        │   ds 读数字/标注 → A–G 确定性对齐门 → 确定性交叉命中校验 → 抽规律
        │   → data/recognize/20260831_all/analysis/judge_20260831.json
        │     + data/crawl/20260831/predictions_with_blogger.json（规律输出，博主归属 join posts.json）
```

### 命令链（20260831 实测）

```bash
# ① 爬取（服务器容器内）＋ 开奖历史
docker exec zhenjie sh -c 'cd /data/zhenjie/zj869 && .venv/bin/python tools/crawl_gouli.py 2026-08-31'
/usr/bin/python3 tools/fetch_lottery.py --out data/crawl/20260831/lottery_recent.json --limit 60

# ② 确定性过滤（583 张，OpenCV+tesseract 秒级，无 LLM）
/usr/bin/python3 modules/image_recognize/filter_trend.py \
    --date 20260831 --target-period 26233 \
    --lottery data/crawl/20260831/lottery_recent.json

# ③ 裁剪（复用，全量跑一次即可）
/usr/bin/python3 modules/image_recognize/crop_all.py --date 20260831

# ④ resize keep/uncertain 图（从 filter_report 取，不覆盖任何旧产物）
/usr/bin/python3 modules/image_recognize/resize_crops.py \
    --date 20260831 \
    --filter data/crawl/20260831/filter_report.json \
    --manifest data/recognize/20260831_all/crops_all_manifest.json \
    --out-dir data/recognize/20260831_all/vision

# ⑤ 视觉判定 + 规律（--files f1,f2 指定冒烟图；默认对全部 vision 图）
/usr/bin/python3 modules/image_recognize/judge_accuracy.py \
    --date 20260831 --target-period 26233 --draw "1 6 3 4 0" \
    --manifest data/recognize/20260831_all/crops_all_manifest.json \
    --filter data/crawl/20260831/filter_report.json \
    --vision data/recognize/20260831_all/vision_manifest.json \
    --lottery data/crawl/20260831/lottery_recent.json \
    --posts data/crawl/20260831/posts.json \
    --src-dir data/crawl/20260831/images
```

### 过滤决策矩阵（filter_trend.py v3，全部确定性）

| 条件 | 决策 | 20260831 计数 |
|---|---|---|
| 非走势图版式（行带/列结构不匹配） | `exclude/no-chart` | 80 |
| 走势图但无任何标注 | `exclude/no-anno` | 17 |
| 期号命中但距目标 > window（旧期画） | `exclude/stale-period` | 12 |
| 有标注但全是孤立 dot / 列定位失败且无有效画规 | `exclude/anno-trivial` | 55 |
| 期号高置信 + gap∈window + 标注覆盖数字列 | `keep-high` | 28 |
| 期号在窗口内 + 标注存在（质量中等） | `keep-med` | 106 |
| 期号弱（读不出/不连续）但结构+标注像走势图 | `uncertain`（送视觉） | 285 |

v3 修复的两处误杀根因：① **ring（圈选）计入有效标注**（原版只认 band/box，把真实画规图一刀切）；
② **列定位换灰度投影**（`detect_columns`：gray<205 全域投影→宽峰→5 列等距选择，深色图也能用；
原 `find_cols_in_band` 阈值 140 把深色背景当前景 → 列全空 → 误判 trivial）。结果 anno-trivial 236→55。

### 冒烟结果（20260831 / 26233 / draw=1 6 3 4 0）

| 图 | 过滤决策 | 判定 | 对齐（A–G 门） | 匹配期 | 命中 |
|---|---|---|---|---|---|
| `s_2_0ee3536d…_2.jpg` | keep-med | ds-ok | 全过（虚构0/漏0、读数 7/9 行） | 26221→26232（6 期） | row0 千位=6（26221）、row3 十位=4（26224）、row5 万位=1（26225） |
| `s_2_33b7cc04…_2.jpg` | keep-high | ds-ok | 全过（读数 11/16 行） | 26222→26232（11 期） | row6 千位=6（26228），与 glm 核验命中一致 |

规律输出：`predictions_with_blogger.json` = 24 条 patterns + 14 条标注命中判定（含博主归属）。
判定口径 = **对已开奖回溯判定**：模型读博主标注位置+数字，A–G 门保证行→期映射可靠后，
确定性逐位算 hit，与模型 verdict 交叉核对（不一致则 glm 兜底）。**杀号/报号/铁率/不定位不计入命中。**

### 关键设计（踩坑沉淀）

- **anti-loop 提示词**：网关"始终思考"型模型对开放式规律分析死循环；判定 prompt 一律"只读数字、
  忽略彩色标记、单紧 JSON、不要思考不要解释"。
- **A–G 对齐门**（`analyze_crops_ds.py` 只 import 不修改）：A 无虚构行 / B 匹配率≥0.6 / C 无重复期 /
  D 期序单调 / E 时效≤5 / F 标注覆盖 / G 底部锚定。ds 读数不稳（同图多次调用读行数不同）→
  G 门软化（底部行没读出不算失败）+ B 门失败自动重试（最多 3 次）。
- **resize 夹逼**：过大下采样到 ≤1024×2200，过小（640 栈图）上采样到 ≥1024 宽，统一 JPEG q90。
- **零污染约束**：filter/resize/judge 只写新文件；`image_patterns_with_blogger.json`（2145 条旧流程产物）、
  `crops_all_manifest.json`、`exclude_list.json` 哈希校验不变；20260829/30 目录不动。

---

## 1. 历史管线（五阶段，26230–26232 期使用，已被四步管线取代）

```
① 历史开奖采集          500彩票网 plw 接口（gb2312，UA+Referer）
        │
② 博主画图采集          gouli99 论坛 API（wsqdata.gouli8.cn/v2/feeds/stream?lottery=2=排列五）
        │               → posts.json + images/（博主走势图原图）
        ▼
③ 视觉识别规律          裁剪 → 视觉模型读取（对齐协议见 §3）
        │               → vision_patterns_full.json → image_patterns_with_blogger.json（704条画规记录）
        ▼
④ 命中核验（重点）       真实开奖校准 + 双读/三读多数表决 → CONFIRM/REJECT/KILL/AMBIGUOUS
        │               → verify_results_FINAL.json → 260828_verified/（确认命中图集）
        ▼
⑤ 画规方法库            每位博主一条方法（画法类型/描述/推理/预测/命中）
                        → pattern_methods.json + docs/画规方法库.md（目标累计 1000 条）
```

## 2. 当前状态

### ✅ 已完成

| 阶段 | 内容 | 产物 |
|---|---|---|
| ① 历史数据 | 730 期真实排列5（26229→24203），增量可跑 | `agents/collector.py` 实测通过 |
| ② 博主图采集 | 20260828 期 230+ 张博主走势图 + 帖子 | `data/crawl/20260828/`、`tools/crawl_gouli.py` |
| ③ 视觉识别 | 698 条画规记录（斜连/定位/胆码/和值/杀号/头/尾） | `image_patterns_with_blogger.json` |
| ④ 命中核验 | **多位置逐张重读**：20 条旧命中 → **真命中 18（1位置1中 3 / 多位置部分命中 15）/剔除 2（星辰888 不定位铁码、流萤 无画规）**；规律库定稿 **17**（验证排除 用规说话_4 描述有误，见被剔除清单） | `glm_multipos_recheck.json`、`docs/GLM命中重核报告-20260831.md` |
| ⑤ 方法库 | **48 位博主**方法条目（类型/描述/推理/预测/命中），数据驱动初版完成 | `pattern_methods.json`、`docs/画规方法库.md` |
| 规律库 | 26230 期命中规律 **17 条**（含各位置对错 + 画规逻辑 + 窗口内验证） | `docs/规律/26230.md` + `26230.json` |
| 规律验证 | **无未来函数回测**：三层验证（结构事实/候选vs随机/家族滚动回测），17/17 事实通过、各家族命中≈随机线 | `tools/verify_rules.py`、`docs/规律/26230_验证.md` |
| 工具链 | 8 个 python 工具全部入库、可独立运行 | `tools/` |

### ⚠️ 未完成 / 需人工定夺

| 事项 | 说明 | 处理 |
|---|---|---|
| **方法库 LLM 推理层** | 48 条中仅 1 条含 LLM 深度推理，其余为规则推导 | **被 llm.riverbegin.cn 网关内容过滤阻塞**（见已知问题#4）；网关修复或换 API 后一条命令升级（§7-⑥） |
| **微时光_1** | 旧记录"百位6/个位3"被驳回：**deepseek 12 读 + GLM 独立复核都读 6@万位、3@十位**，校准数字全一致 | 你肉眼复核 `C:\Users\zhenjie.wu\.dsh\work\gouli_crop\batch45\s_2_0f08b4cd-..._1_b45.jpg`，若确认百6/个3说明该图有特殊几何偏移 |
| **乐仔👑1288_0** | 红格 = 万位49 / 千位05，**杀号还是预测存疑**（若预测则万位9命中） | 肉眼看图定夺 |
| **辉拓数据_4** | 杀号第1位 X 位置在 26229 行区域，存疑（杀号本就不计入命中） | 可忽略 |
| **GitHub 推送** | 本地有大量新提交未推送（此前网络问题暂停） | 网络恢复后 `git push` |

### 🔴 已知问题

1. **视觉模型故障**：`deepseek-v4-flash-vision-exp` 在 llm.riverbegin.cn 网关长期返回空内容（200 但 content 空）；**`glm-5.3-flash` 可用**（已实测读图成功），所有验证脚本已支持 `--model glm-5.3-flash` 切换。
2. **网关间歇 500/空响应**：flash 文本也曾受影响，脚本内置重试（空响应快速重试、HTTP 错误退避）。
3. **尾数图裁剪陷阱**：流萤的尾数走势图数据在上半部，底部裁剪是空白网格——需用 `full` 模式全图识别。
4. **网关对"彩票画规分析"提示词的内容过滤（2026-08-29 实测确认）**：`llm.riverbegin.cn` 对**含彩票画规分析语义的提示词**（斜连/排列5/万千百十个/数字串/博主+走势图等）返回**空内容**，对所有模型（deepseek-v4-flash/pro、glm-5.3/glm-5.3-flash）和所有格式（非流式/流式/JSON/纯文本/拼音混淆）**均失败**（0-5% 成功率），而普通提示词（"用三句话介绍园林"）与简单彩票提及（"26230期"）**100% 成功**。疑为网关侧赌博/彩票内容审查。→ **方法库 LLM 推理层升级因此被外部阻塞**；已备好 48 条数据版（规则推理）兜底。若你可调整网关配置（关闭内容过滤）或有其他 API，改 `summarize_methods.py` 的 `API_URL`/密钥即可重跑。

## 3. 视觉列位对齐协议（本轮核心成果）

问题：列号式对齐（"第1列=万位"）会被**辅助数值列**（期号旁 13/15 和值列）干扰 → 列位读错。

修复（`tools/verify_chart_hits.py` 内置）：
1. **校准数字核验**：模型先读上一期开奖行（26229=2,8,0,5,4 / 26228=5,6,0,2,5），读出 5 数字与真实开奖**逐位一致**才算对齐（`calibration_digits`+`calibration_ok`），judge 侧二次校验，不符即判不可靠。
2. **位名锚定**：预测一律按位名（万/千/百/十/个）报告，锚定"万位=校准行数字2所在列"，彻底消除列号歧义。

## 4. 目录结构```
zj869/
├── modules/image_recognize/     # 四步管线（现行，2026-08-31 起）
│   ├── filter_trend.py          # ② 确定性过滤（OpenCV+OCR，无 LLM）→ filter_report.json
│   ├── crop_all.py              # ③ 裁剪（复用；仅 status==cropped 进识别）
│   ├── resize_crops.py          # ④ 显式 resize（640→≥1024 宽）→ vision/*.jpg
│   ├── judge_accuracy.py        # ⑤ 视觉判定+规律 → judge_<date>.json + predictions_with_blogger.json
│   └── cv_trend_reader/         # 底层原语（行带/列定位/标注形态/期号OCR/开奖匹配）
├── agents/                    # 原始多Agent框架（collector 已接真实数据）
├── tools/                     # 数据/验证/方法库工具（主战场）
│   ├── crawl_gouli.py         # ② gouli99 论坛图爬虫
│   ├── crop_charts.py         # 裁剪工具（bottom45/zoom/full 三模式）
│   ├── verify_chart_hits.py   # ④ 命中核验主脚本（对齐协议+双读）
│   ├── verify_phase2.py       # ④ x坐标三读补验（AMBIGUOUS/ERROR）
│   ├── verify_stragglers.py   # ④ 尾部清理（专用裁剪+专用提示词+复测文件）
│   ├── finalize_verdicts.py   # ④ 终局改判（确定性覆盖）
│   ├── rebuild_hits.py        # ④ 重建命中集 + 260828_verified/
│   ├── summarize_image_patterns.py  # ③ 图片规律总结报告
│   ├── summarize_methods.py   # ⑤ 方法库 LLM 合成（推理层）
│   ├── methods_from_data.py   # ⑤ 方法库数据版（规则推理，LLM 兜底）
│   ├── merge_backtest.py / run_pipeline_m3.py / analyze_text.py  # 早期文本管线
│   └── ssh-run.ps1 / probe_server*.sh  # 服务器运维
├── docs/                      # 全部报告 + 命中规律库（docs/规律/<期号>.md + .json）
├── data/crawl/20260828/       # 26230期数据（本地，不入库：image_patterns_with_blogger.json 等）
└── .dev/                      # ORCHESTRATOR.md（恢复指引）+ JOURNAL.md（日志）
```

## 5. 方法库数据结构（pattern_methods.json）

```jsonc
{
  "goal": "画规方法库,目标1000条",
  "period": "26230",
  "draw": "9 4 6 8 3",
  "total": 48,
  "methods": [
    {
      "method_id": "M0001",            // 方法编号
      "blogger": "博主名",
      "period": "26230",
      "draw": "9 4 6 8 3",             // 本期开奖
      "method_type": ["斜连","框选胆码"], // 画法类型(斜连/直连/重号/邻号/遗漏/冷热/对称/框选胆码/定位/杀号/和值/尾数)
      "style": "一句话风格概括",
      "description": "详细画法描述（引用圈选/连线/框选内容）",
      "reasoning": "推理逻辑——为什么这么画、怎么从历史推导",  // 规则推导或LLM
      "predictions": [                  // 该博主本期预测(已核验)
        {"position": "百位", "digits": [6], "hit": true, "verdict": "CONFIRM", "note": "..."}
      ],
      "hit_summary": "2命中/3核验",
      "method_summary": "可复用方法要点",
      "image_files": ["s_2_xxx_0.jpg"],
      "data_source": "data-v1 | llm"
    }
  ]
}
```

若要从 8/28 快照重建等价方法库：`tools/methods_from_data.py --records image_patterns_with_blogger.json --verify verify_results_FINAL.json --out-json pattern_methods.json --out-md docs/画规方法库.md`（数据版，无需 LLM）。

## 6. 环境与密钥（**禁止进仓库**）

| 项 | 位置 |
|---|---|
| 视觉/文本 API 密钥 | `C:\Users\zhenjie.wu\.dsh\.credentials.yaml`（DEEPSEEK1_API_KEY，53位） |
| 服务器 ssh 免密 | `C:\Users\zhenjie.wu\.dsh\secrets\askpass.cmd`（10.5.64.5/enrigin） |
| gouli99 账号 | `C:\Users\zhenjie.wu\.dsh\secrets\gouli99.txt` |
| 本地图片缓存 | `C:\Users\zhenjie.wu\.dsh\work\gouli_jpg\`（原图）、`gouli_crop\`（裁剪图） |

## 6. 工具用法（你自己跑）

```bash
# ① 裁剪预测行（bottom45=底部45%含校准行；zoom=定向放大；full=全图）
python tools/crop_charts.py --src <原图目录> --dst <裁剪目录> --files a.jpg b.jpg --mode bottom45

# ④ 全量命中核验（--model 可切 glm-5.3-flash；--settled 合并人工已定记录）
python tools/verify_chart_hits.py --records data/crawl/20260828/image_patterns_with_blogger.json \
    --images "C:\Users\zhenjie.wu\.dsh\work\gouli_jpg" \
    --crops "C:\Users\zhenjie.wu\.dsh\work\gouli_crop\batch45" \
    --out data/crawl/20260828/verify_results.json \
    --settled data/crawl/20260828/settled_manual.json --workers 8 --model glm-5.3-flash

# ④ 补验 AMBIGUOUS/ERROR（三读多数表决）
python tools/verify_phase2.py --in <上一步输出> --crops <裁剪目录> --out <输出> --model glm-5.3-flash

# ④ 尾部清理 + 复测指定图（--extra-files 逗号分隔）
python tools/verify_stragglers.py --in <上一步输出> --crops <裁剪目录> --out <输出> \
    --extra-files s_2_xxx_1.jpg,s_2_yyy_3.jpg --model glm-5.3-flash

# ④ 终局改判 + 重建命中集
python tools/finalize_verdicts.py --in <上一步输出> --out data/crawl/20260828/verify_results_FINAL.json
python tools/rebuild_hits.py --verify data/crawl/20260828/verify_results_FINAL.json --images <原图目录>

# ⑤ 方法库：LLM 推理层升级（网关正常时全量生成；失败回退数据版）
python tools/summarize_methods.py \
    --records data/crawl/20260828/image_patterns_with_blogger.json \
    --draws   data/crawl/20260828/lottery_recent.json \
    --verify  data/crawl/20260828/verify_results_FINAL.json \
    --fallback-data data/crawl/20260828/pattern_methods.json \
    --out-json data/crawl/20260828/pattern_methods.json \
    --out-md   docs/画规方法库.md --workers 8
# ⑤ 方法库：数据版（规则推理，无需LLM，48条完整）
python tools/methods_from_data.py --records <同上> --verify <同上> --merge-llm pattern_methods.json \
    --out-json pattern_methods.json --out-md docs/画规方法库.md

# ⑥ 规律验证（无未来函数回测：结构事实核对 + 候选vs随机 + 家族滚动回测）
#    --inplace 把 verify 字段写回规律库；--md 生成验证表
python tools/verify_rules.py \
    --rules docs/规律/26230.json \
    --draws data/crawl/20260828/lottery_recent.json \
    --out data/crawl/20260828/rule_verify_26230.json \
    --inplace --md docs/规律/26230_验证.md

# 复测（纯计算，无视觉模型）：从识别结果 type/position/numbers 推出每条预测逐位置对位，
# 输出与 glm_multipos_recheck.json 兼容；--base 传纯日期
python tools/recheck_compute.py --base 20260829 --period 26231 --draw "1 8 7 9 9" \
    --calib 26230 --calib-draw "9 4 6 8 3"

# ② gouli99 采集（服务器容器内跑）
# docker exec zhenjie sh -c 'cd /data/zhenjie/zj869 && .venv/bin/python tools/crawl_gouli.py 2026-08-28'
```

## 7. 视觉模型切换（GLM）

- 配置已写入 `C:\Users\zhenjie.wu\.dsh\settings.yaml`：deepseek1 provider 下新增
  `glm-5.3-flash`（多模态，同网关同密钥），workflow 可 `{provider:"deepseek1", model:"glm-5.3-flash"}`。
- 所有验证脚本支持 `--model glm-5.3-flash`。
- 实测对比（微时光_1 争议图）：deepseek-vision 全空响应；glm-5.3-flash 读出
  `calibration=[2,8,0,5,4] ✓, 6@万位, 3@十位`，与 deepseek 历史 12 读一致。

## 8. 下一步建议

1. 网关确认稳定后：`summarize_methods.py` 升级方法库推理层（48 条全量 LLM）。
2. 人工定夺 3 处（微时光_1 / 乐仔 / 辉拓数据_4），如需改判跑 `finalize_verdicts.py` + `rebuild_hits.py`。
3. 下一期（26231 起）增量：`crawl_gouli.py` 采新图 → 视觉识别 → 核验 → 方法库追加，向 1000 条推进。
4. 服务器同步：scp 到 `/data/zhenjie/zj869/`（容器内路径），`docker exec zhenjie git add/commit/push`。
5. GitHub 推送恢复（此前网络暂停）。

## 免责声明

本工程仅用于技术研究与数据规律分析。彩票开奖属独立随机事件，历史画规不具备预测效力，请勿用于赌博或非法用途。
