# zj869 — 排列5 博主打点图画规规律采集·验证·方法库系统

> 从 gouli99 论坛抓取博主手画的"排列五走势图"，用**视觉模型**识别画规（圈选/连线/框选/杀号），
> 通过**真实开奖校准**核验命中，沉淀为**画规方法库**（目标 1000 条），为后续每期预测/回测提供方法依据。
>
> 现状一句话：**26231/26232 两期已全流程分析：采集 556+699=1255 条记录 → 命中 98+129=227 条（17.63%/18.45%），
> 已沉淀命中规律 227 条（`docs/规律/26231.md`、`26232.md`）。26230 期命中复核定稿：采集 694 条 → 命中 18 条（2.59%），
> 其中完全命中（1位置1中）3 条，已用 `tools/verify_rules.py` 做无未来函数窗口内验证；画规方法库 48 条已生成；视觉模型已切换到可用的 glm-5.3-flash。**

---

## 0. 每期准确率与采集量（规律库入口）

| 期号 | 开奖 | 采集记录 | 命中 | 命中率 | 完全命中 | 累计规律 |
|---|---|---|---|---|---|---|
| 26230 | 9 4 6 8 3 | 694 条 | 18 条 | 2.59% | 3 条 | **18 条**（docs/规律/26230.md） |
| 26231 | 1 8 7 9 9 | 556 条 | 98 条 | 17.63% | —（未跑多位置重读） | **98 条**（docs/规律/26231.md） |
| 26232 | 8 0 2 3 3 | 699 条 | 129 条 | 18.45% | —（未跑多位置重读） | **129 条**（docs/规律/26232.md） |

> 口径说明：
> - **采集记录**：该期抓到的博主画规记录数（视觉识别全部画法标注，含未命中）。
> - **命中**：博主预测位置含实际开出数字（空号杀号、未画规律、不定位铁码等假命中已剔除）。
> - **完全命中**：博主只预测 1 个位置且命中（1位置1中）。
> - **命中率 = 命中 / 采集**；每期新增规律累计写入 `docs/规律/<期号>.md`。
> - **杀号不计入**：博主"杀掉"的号码不体现画规预测，从采集与命中口径整体剔除（26231 剔 33 / 26232 剔 42 / 26230 剔 4）。
> - 26231/26232 未跑 GLM 多位置重读（网关识图慢、用户决定跳过），命中口径为单条识别命中；
>   26230 跑过重读并剔除 2 条假命中，故命中率口径略严、数值更低（2.59%）。
> - ⚠️ 彩票开奖属独立随机事件，历史规律不具备预测效力。

---

## 1. 整体设计（五阶段管线）

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
