# zj869 — 排列5 博主打点图画规规律采集·验证·方法库系统

> 从 gouli99 论坛抓取博主手画的"排列五走势图"，用**视觉模型**识别画规（圈选/连线/框选/杀号），
> 通过**真实开奖校准**核验命中，沉淀为**画规方法库**（目标 1000 条），为后续每期预测/回测提供方法依据。
>
> 现状一句话：**流程已改造为六步管线「爬取 → 确定性过滤 → 裁剪 → resize → 视觉判定 → 画规自证复现」，20260831 期（26233）583 张图跑通：过滤 keep 134 / uncertain 285 / exclude 164，冒烟 2 张全部 ds-ok（A–G 对齐门全过、匹配 6/11 期、命中已判定）；命中图进画规自证复现（读整张原图画规 + 值保真/逻辑自洽：26231 试点已跑通，26233–26235 已全量⑥⑦判定定稿，见 0a 表）。旧五阶段管线（26230–26232）成果：26231/26232 采集 831 条、**命中 0（单源视觉读数未独立复核，按口径全部剔除）**；26230 采集 694 条、**命中 6 条（单押口径，仅单码必中计命中）**，画规方法库 48 条已生成。**

---

## 0a. 历史成果：每期准确率与采集量（规律库入口）

| 期号 | 开奖 | 采集记录 | 命中 | 命中率 | 完全命中 | 累计规律 |
|---|---|---|---|---|---|---|
| 26230 | 9 4 6 8 3 | 694 条 | 6 条 | 0.86% | 6 条 | **6 条**（docs/规律/26230.md，单押口径） |
| 26231 | 1 8 7 9 9 | 80 条 | 4 条 | 5.00% | 4 条 | **4 条**（docs/规律/26231.md，GLM 校准行锚定单押口径） |
| 26232 | 8 0 2 3 3 | 471 条 | 0 条 | 0.00% | 0 条 | **0 条**（docs/规律/26232.md） |
| 26233 | 1 6 3 4 0 | 36 条 | 5 条 | 13.89% | 5 条 | **1 条**（docs/规律/26233.md，2026-09-03 审计修正：剔除 2 假命中→命中 5；伊晴 对称斜连镜像两数和=5,6,7 等差、十8=树顶自身 → 列规律，余 4 巧合） |
| 26234 | 3 0 1 2 9 | 28 条 | 1 条 | 3.57% | 1 条 | **0 条**（docs/规律/26234.md，命中=事实但画规规律可推 0 条 → 1 条巧合） |
| 26235 | 7 7 9 6 7 | 33 条 | 1 条 | 3.03% | 1 条 | **0 条**（docs/规律/26235.md，命中=事实但画规规律可推 0 条 → 1 条巧合） |

> 口径说明：
> - **采集记录**：该期博主的画规记录数（仅"走势图圈选"且**开奖前发帖**；杀号、报号/铁率文字截图不体现画规，从采集口径整体剔除）。
> - **命中归零（2026-09-02 定案）**：26232 的图上标记**只经过单源视觉识别读数、从未独立二次识读**（当年 GLM 多位置重读未跑成）。手绘标记可被同图解释为任意数字（"10 张一样的图可预测不同数字"），单源读数不可验证 → 按 `--require-verified` 口径**全部剔除，命中 0、规律 0**。仅保留采集口径的原始描述，不吹命中。
> - **26231 重新定案（2026-09-02）**：改用 **GLM-5.3-flash 读博主目标期行窄条**（`read_blogger_prediction.py`，**校准列锚定**到 `filter_report.cols` + anti-loop 提示词 + 批 1 窄任务），**复读 5/5 复现** + **纯算术单押判定**（`verify_blogger_prediction.py` → `export_blogger_prediction.py`）→ 单码采集 **80** / 命中 **4（5.00%）** / 规律 **4 条**。**修正要点**：① 旧 DS-Vision 读数**系统性列位偏移**（跳过期号/和值例→万千百十个右移 1 格），旧 5 条"命中"里 4 条实为**错位假命中**（`1baf97fb_2/_5/_7` 百7、`2ecff643_0` 千8，校准列锚定后正确判 miss）；② 校准后找回旧管线错/漏判的 **4 条真命中**（用规说话 万1、富老师 万1、生活很无奈 万1、`487199630` 十9）；③ **位置名绝不盲信 GLM 自报**——那是循环校验（模型说"百位"就查百位）。命中位置一律经**校准行锚定**（前一期已知开奖 **26230=9 4 6 8 3** 印在图上的数字列位反推万千百十个真实列），固化进 `verify_blogger_prediction.py --position-overrides`（逐条记 `position_source`：calib-anchor / glm-read）。据此**富老师（图 _2）由假命改判 MISS**：GLM 原读"百位=7"（百实开7故误判命中），校准行锚定后"7"实为千位=7、千实开 8 → 单码错位未中。⚠️ 这 4 条命中为 **GLM 单源读数经算术对位 + 校准行锚定（模型无关）**，未做第二视觉模型交叉识读。命中原图读规律为下一步（stage ③）。
> - **开奖前发帖**：排列5 每日 **21:30 开奖**，按日期爬取的帖子含当晚开奖后才发的图——博主已知道本期开奖，图上圈的是**已知号码**（复盘）或预测**下一期**，不能算本期预测（`tools/export_rules.py --cutoff 21:30`，26231 剔 5 条复盘命中候选、26232 剔 23 条）。
> - **命中**（若日后补跑独立二次识读再启用）：博主在走势图上画出、且**恰好押 1 个数字**、**该位置实际真的开出**该数字，并经第二来源复核（空号杀号、未画规律、不定位铁码、报号/铁率、多码宽网、单码错位等假命中已剔除）。
> - **命中率 = 命中 / 采集**；每期新增规律累计写入 `docs/规律/<期号>.md`。
> - **杀号不计入**：博主"杀掉"的号码不体现画规预测（26231 剔 33 / 26232 剔 42 / 26230 剔 4）。
> - **报号/铁率不计入**：文字预测截图类（博主直接打字报数/缩水推荐，无画规）从采集与命中整体剔除（26231 剔 62 / 26232 剔 59）。
> - **不定位不计入**：无位置的胆码全盘/组合推荐，非定位画规，不算命中。
> - **多码宽网不计入**：≥2 候选的"候选池式"画规推不出本期唯一结果（26231 剔 15 / 26232 剔 33）。
> - **单码错位不计入**：单押 1 码但数字开在别位，仅靠全盘碰巧命中（26231 剔 5 / 26232 剔 5）。
> - 26230 已按最终**单押口径**重导：博主一位只写一个数字且该位实开该数才计命中，18 旧多位置命中 → **6 条**（熊大出品 万9、微时光 十8、默言言心 万9、二叔×2 万9、小屁股 万9）；双选（4/9、1/6、49）与文字报号、缺图不可核验一律剔除。
> - **尾池默认并入（2026-09-03 起默认口径）**：前一日 21:30(上期开奖)后发的下一期预测落在前一日目录，跑当前期会由 `tools/include_prevday_tail.py` **自动并入**前一日尾池再一起 ④verify ⑤export —— 跑一个期 = 一条命令，无需手动加尾池。
> - ⚠️ 彩票开奖属独立随机事件，历史规律不具备预测效力。

---

## 0b. 命中基准：博主目标期行手写单押（26231 试点，2026-09-02）

这一条线才是**命中的正身**——命中只认**博主写在目标期行的手写数字**（非"看圈/看历史落点/程序自摸规律"）。仅对开奖前发帖的目标期行窄条走：

```
① 裁目标期行窄条 + 开奖前发帖过滤(21:30 cutoff)
   modules/image_recognize/extract_prediction_strip.py
第二道门：只读它 82 条(复盘 2 条已剔) → 开奖前 80 条
      ▼
② GLM-5.3-flash 读博主目标期行手写（每批 1 条窄任务，校准列锚定 `filter_report.cols` + anti-loop 提示词；复读 5/5 复现）
   modules/image_recognize/read_blogger_prediction.py --strips … --batch 1 --model glm
      │   输出 data/crawl/20260829/blogger_predictions.json
      │   （predicted_positions=[{位置,候选,标注方式}]；和值/组选/胆 → reject_reason）
      ▼
③ 单押命中判定（纯算术，零视觉；**位置经校准行锚定，不盲信 GLM 位置名**）
   modules/image_recognize/verify_blogger_prediction.py --position-overrides data/crawl/20260829/position_anchor_overrides.json
      │   位置名来源 position_source=calib-anchor（26230=9 4 6 8 3 校准行锚定）/ glm-read（未校准）
      │   单码采集 = hit+miss（博主一位只写 1 数且能定位）；多码/和值(C)/空读(B)/不定位剔除出分母
      │   → blogger_predictions_verify.json  单码采集 80 / 命中 4（富老师_2 百7 错位改判 miss）
      ▼
④ 导出 docs/规律/26231.json + .md
   tools/export_blogger_prediction.py   → 采集 80 / 命中 4 (5.00%) / 规律 4 / 剔除 23
```

## 0. 现行全流程（六步管线，2026-08-31 起，从爬取到画规自证复现输出规律）

改造目标：爬完一期数据后不再把每张原图都喂慢速视觉模型，先用**确定性 OpenCV+CNN+OCR** 过滤掉
"没在近期待开奖历史上画规律"的图，再把真正画了规的图交给视觉模型做**对已开奖回溯判定**，最后经**画规自证复现**沉淀为规律。

### 0.1 六步总览（大功能一张表）

| 步 | 脚本 | 大功能 | 是否用 LLM |
|---|---|---|---|
| ① 爬取 | `tools/crawl_gouli.py` + `tools/fetch_lottery.py` | 抓博主走势图原图 + 排列五开奖历史（按时间切期 + 期号备注双重验证） | ✗ |
| ② 确定性过滤 | `modules/image_recognize/filter_trend.py` v3 | 四信号置信分级（OpenCV + CNN 期号 OCR + tesseract 边界复核），剔掉"没画规律"的图 | ✗ |
| ③ 裁剪+拼接 | `modules/image_recognize/crop_all.py` | 字色分类 + 标注行检测 + 行条**拼接**成栈图 | ✗ |
| ④ resize | `modules/image_recognize/resize_crops.py` | 栈图 640→≥1024 宽（③④是一个连续预处理，可视为一步） | ✗ |
| ⑤ 视觉判定+命中 | `modules/image_recognize/judge_accuracy.py` | 读**栈图（拼接结果）**行数字 → A–G 对齐自校正 → 逐位**确定性命中**（含无未来函数/防自证自检） | ✅ ds（glm 兜底） |
| ⑥ 画规自证复现 | `modules/image_recognize/read_guihua.py` + `tools/reproduce_guihua.py` | 读**整张命中原图**的博主画规链 → 值保真 + 逻辑自洽复现 → 沉淀真画规 | ✅ glm |

### 0.2 分步拆解（大功能 → 子功能 → ⚠️ 注意点）

#### ① 爬取博主图 + 开奖历史

**大功能**：拿一期数据的两块原料——博主走势图原图 + 真实开奖历史，并正确**归期**。

**拆解**：
- `crawl_gouli.py`：gouli99 论坛 API（`wsqdata.gouli8.cn/v2/feeds/stream?lottery=2` 排列五），分页 `start/count` 拉全帖子，按 `create_time` 过滤 `[start, end]`；下载图片（`origin` 原图优先，其次 `750`/`360`）→ `posts.json` + `images/s_2_<uuid>_<n>.jpg` + `images_map.json`。
- `fetch_lottery.py`：500彩票网 `history.php`（gb2312），解析 `t_tr1` 行 → `[{period, numbers, date}]`，最新在前 → `lottery_recent.json`（默认 60 期）。

**⚠️ 注意点（日期切期 + 双重验证）**：
- **开奖时间切期**：排列五每日 **21:30 开奖**，开奖后发帖 = 复盘/预测下一期（图上圈的是已知号码），不能算本期预测；经验上 **22:00 后发的图基本是下一期**。下游靠 `--cutoff` 剔除开奖后发帖（`tools/export_rules.py --cutoff` 默认已统一为 **21:30**）。
- **双重验证 = 时间切期 × 期号备注**：帖子正文有时显式写期号（如"排列五26233期"），应拿它交叉核验时间切期是否一致——实测仅约 **7/694 帖**正文带显式期号，多数帖只能靠 `create_time` 切期；两者冲突时以期号备注为准并标记。
- `crawl_gouli` 要在**服务器容器内**跑（`.venv` 环境），需 UA + `Referer: gouli99.cn` 头；`fetch_lottery` 是 gb2312 编码、需 UA + `Referer: 500.com`。

#### ② 确定性过滤（filter_trend.py v3，无 LLM）

**大功能**：用确定性方法把"没在近期待开奖历史画规律的图"剔掉，减少送视觉模型的数量（583 → 保留 keep 134 + uncertain 285，剔除 164）。

**拆解（多信号分级，非单信号一刀切）**：
- **S1 期号锚定**（`period_pairs`/`period_confidence`）：读底部多行期号 + 与开奖历史做多期连续性校验；≥2 行都匹配开奖才高置信。
- **S2 标注存在性**：`process_one` 的饱和像素判定（行窗内饱和像素 > 200）。
- **S3 标注质量分级**（`annotation_quality`）：`detect_annotations` 形态分类（band/box/ring/dot）+ 是否覆盖数字列。
- **S4 列覆盖校验**（`detect_columns` v3）：灰度 <205 全域投影 → 宽峰 → 选最等距 5 列；标注 x 中心命中列才算有效画规。

**具体实现技术**：
- **OpenCV**：灰度投影列定位（`detect_columns`，`gray<205` 掩码全域投影 → 宽峰剔网格线/面板 → 等距选 5 列）；饱和度掩码标注检测（`saturation_mask`，`max-min>80 && max>120`）。
- **CNN 期号 OCR**：`model/digit_cnn.py` — PyTorch 小模型，**32×48 灰度输入、3×Conv(32/64/128)+GAP+Linear(10)、~94k 参数**，合成字体训练（`train_digits.py`）。`ocr_digits_cnn` 多档阈值连通域切分 → 逐字分类；`conf<0.6` 或 top1-top2 差 <0.15 → 标 uncertain。
- **tesseract**：子进程多档 `--psm`/upscale/阈值，**仅做 stale-period 边界复核**（不是主路）。

**⚠️ 注意点**：
- **主路 OCR 固定 CNN**（`OCR_ENGINE` 默认 cnn），**不要用 auto**——auto 的"CNN 失败→tesseract 逐行兜底"会在大量 CNN 读不出的图上退化成旧 tesseract 全成本（实测 >13min 跑不完 583 张）。
- **tesseract 只做 stale 边界复核**：CNN 单字 ~79% 会把近期期号尾位读错 1 位 → 锚点跳出 ±5 窗口 → 误排除不可逆；只对"将排除为 stale"的图（每期 ~26-40 张）复核。
- **v3 修复两处误杀**：① ring（圈选）计入有效标注（原版只认 band/box 把真实画规图一刀切）；② 列定位换灰度投影（原 `find_cols_in_band` 阈值 140 把深色背景当前景 → 列全空 → 误判 trivial）。
- 期号 OCR 阈值必须够高（180~235）才抓得到浅灰期号数字。
- 白名单正则 `^s_2_<uuid>_<n>\.(png|jpg|jpeg)$` 防临时图污染。

#### ③ 裁剪 + 拼接 → ④ resize（预处理，可视为一步）

**大功能**：把博主标注行裁剪出来、**拼接成一张栈图**、放大到视觉友好尺寸——三个连续动作合成一步预处理，产出视觉模型直接可读的单图。

**拆解**：
- `classify_digit_color`：用哪种字色掩码能检出最规整行带 → 判字色（绿/红/蓝/黑）。
- `detect_rows`：行带分段 → 行网格（标准竖版相位行梳 / 紧凑图实际行距 / 稀疏竖版兜底 pitch=136）。
- `saturation_mask` + `annotated_rows`：强色标注行窗内饱和像素 >200 → 标注行。
- **`build_stack` 拼接**：把多个标注行行条**按行号竖排拼成一张栈图**（`02_annotated.png` 标注行栈，含红色 row 标签）——这是喂给视觉模型的输入，不是逐行/逐格喂；另出 `01_rows.png` 全行栈、`03_debug.png` 原图画框核对。
- resize 夹逼：`scale = min(1, max_w/w, max_h/h)`，过大 INTER_AREA 下采样、过小（<1024 宽）INTER_CUBIC 上采样到 ≥1024 宽，JPEG q90。

**⚠️ 注意点**：
- **拼接是关键**：N 个标注行拼成一张栈图，视觉模型一次读完（比逐行裁剪省 90min+ 的纯视觉定位）。
- 栈图固定 640 宽、最大 2200 高；**640 宽浅色数字读不清会死循环**，必须放大到 ≥1024 宽（放大后 33s 可读）——resize 这步不能省。
- 行窗半高随行距缩放（`row_half = min(68, pitch*0.45)`），紧凑图避免相邻行窗重叠。
- `--filter` 传 filter_report 时只裁剪 keep/uncertain 图；不覆盖任何旧产物。

#### ⑤ 视觉判定 + 命中（judge_accuracy.py）

**大功能**：视觉模型**只读栈图（拼接结果）里的行数字**，其余（对齐/标注位/命中）全部确定性计算；**命中图作为⑥画规自证复现的输入**（不抽机器候选规律）。

**确认**：是的——⑤ 直接读 resize 后的 `02_annotated.png`（标注行栈 = ③④ 的拼接产物），**不读原图**。

**正确判定逻辑（读对了才算）**：
- `self_correct_safe`：**row0 用已知最新期锚定**，逐行读数与 `lottery_recent.json` 精确匹配**自校正**（读错的行被开奖历史纠正）。
- **A–G 对齐门**（`validate_alignment`）：A 无虚构行 / B 匹配率≥0.6 / C 无重复期 / D 期序单调 / E 时效≤5 / F 标注覆盖 / G 底部锚定——7 项全过才认"行→期映射可靠"。
- `deterministic_hits`：命中 ⟺ 该位读数 == `target_draw[p]`（纯算术，零模型）。

**拆解**：
- `build_judge_prompt`：prompt 只问"读这些行的数字"，**零标注语义**。
- Pass1 **ds**（`deepseek-v4-flash-vision-exp`）读行数字（空返回/读行不足重试 2 次）→ `normalize_rows` + `self_correct_safe` + `validate_alignment`。
- `deterministic_annotations`：灰度投影 5 列 + 每行饱和 run → 标注位（0=万…4=个）。
- `run_hits`：逐位确定性命中判定（命中 ⟺ 读数==`target_draw[p]`）；无未来函数自检（含本期行自证剔除）。
- Pass2 **glm** 兜底（`--glm-fallback`，默认关闭，glm 对栈图 60-180s 慢）。

**⚠️ 注意点**：
- **prompt 里一旦出现"标注/透过标注/圈选色带"等字样，模型会启动"识别博主预测标注"子任务 → 推理死循环吃光 max_tokens**；必须只保留"读数字+忽略彩色标记"。
- 模型只承担行数字视觉读取；标注位置/数字/命中**全部确定性判定**。
- ds `max_tokens=16000`、单次有界；网关断连抛 `DsConnError`（不无限重试）。
- **G 门软化**：底部标注行"没读出"（read=None）不算失败——"没读到"不是"读错"。
- 输出 `judge_<date>.json`（行读数/标注位/命中）+ 命中图集（供⑥读原图画规）。

#### ⑥ 画规自证复现（read_guihua.py + reproduce_guihua.py）

**大功能**：⑤ 只判定了"命中"，但命中的数字是不是**博主亲手画的规律**推出来的，还没证——⑥ 就是读**整张命中原图**的博主画规链（线/圈/连线轨迹），做**值保真 + 逻辑自洽**自证复现：能由规律库确定性推出预测才算真画规，否则判巧合/乱画。

**拆解（两步）**：
- **`read_guihua.py` 读原图画规**：读**整张命中原图**（画规跨多行，不能用裁目标行的窄条）→ GLM-5.3-flash 结构化 prompt ×2 复现 + 叙事 prompt 交叉验证 → 输出画规类型/画笔元素/画法描述/推导逻辑/预测 → `guihua_<period>_reads.json`。
- **`reproduce_guihua.py` 自证复现（值保真 + 逻辑自洽）**：
  - ① **值保真**：链项必须等于权威开奖表（防读错）。
  - ② **逻辑自洽**：预测必须由规律库（`swap` 交换 / `slant` 斜连 / `repeat` 签名重复）**机械复算**推出；推不出 → 判"巧合命中"，即使命中也不列为规律。
  - ③ **严格链内**：只从博主画的那张图推导，**绝不扫开奖历史表找先例**（防幻觉总开关）。

**verdict**：`ok`（自洽命中）/ `coincidence`（命中但无规律可推，不列规律）/ `reproducible-no-hit`（复现但未中）/ `ds-fail`（读错，需 GLM 兜底重读）。

**⚠️ 注意点**：
- 读**整张原图**，不是⑤的标注行栈图——画规跨多行，窄条读不出线/圈/连线。
- **校准行锚定**（calib 26230=9 4 6 8 3 确认列位），**位置名绝不盲信 GLM 自报**（模型说"百位"就查百位是循环校验）。
- **签名串重复是合法规律**：博主把 `2,9,1` 画两次、或 `1,4,2→1` 千位万位各画一遍，是博主亲手画的轨迹，按 `repeat` 计——**≠ 扫开奖历史表找先例**（那是 banned）。
- **值保真只验真，必须加逻辑自洽**：任何一串真实开奖数字都满足值保真，否则红线穿过的 5/9 也当成圈点（链条超伸）。
- **读轨迹线要读全**：签名串读不全会误判"巧合"。
- 26231 试点终修：4 命中/5.00%（生活很无奈 repeat 万1、富老师 repeat 万1、487199630 swap 十9、用规说话=巧合不列）。

### 命令链（20260831 实测）

```bash
# ① 爬取（服务器容器内）＋ 开奖历史
docker exec zhenjie sh -c 'cd /data/zhenjie/zj869 && .venv/bin/python tools/crawl_gouli.py 2026-08-31'
/usr/bin/python3 tools/fetch_lottery.py --out data/crawl/20260831/lottery_recent.json --limit 60

# ② 确定性过滤（583 张，OpenCV 投影/掩码 + CNN 期号 OCR + tesseract 仅 stale 边界复核，无 LLM）
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

# ⑥ 画规自证复现（读整张命中原图画规 + 值保真/逻辑自洽；26231 试点已跑通，26233 待跑）
#   ⑥a 读原图画规（命中图，GLM 读线/圈/连线轨迹）
/usr/bin/python3 modules/image_recognize/read_guihua.py \
    --period 26231 --draw "1 8 7 9 9" --calib 26230 --calib-draw "9 4 6 8 3" \
    --hits data/crawl/20260829/评审_26231命中5/hits.json \
    --images data/crawl/20260829/评审_26231命中5/images \
    --out data/crawl/20260829/guihua_26231_reads.json --per 2 --workers 6
#   ⑥b 自证复现（值保真 + 逻辑自洽）
/usr/bin/python3 tools/reproduce_guihua.py \
    --json data/crawl/20260829/guihua_26231_reproducible.json \
    --lottery data/crawl/20260829/lottery_recent.json
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

命中判定（⑤ judge_accuracy）：冒烟 2 图 → 命中与标注位已判定。
判定口径 = **对已开奖回溯判定**：模型读博主标注位置+数字，A–G 门保证行→期映射可靠后，
确定性逐位算 hit（命中 ⟺ 读数==`target_draw[p]`）+ 无未来函数自检（含本期行自证剔除）。**杀号/报号/铁率/不定位不计入命中。**
命中图进⑥画规自证复现（读整张原图画规 + 值保真/逻辑自洽），26231 试点终修 4 命中/5.00%。

### 关键设计（踩坑沉淀）

- **anti-loop 提示词**：网关"始终思考"型模型对开放式规律分析死循环；判定 prompt 一律"只读数字、
  忽略彩色标记、单紧 JSON、不要思考不要解释"。
- **A–G 对齐门**（`analyze_crops_ds.py` 只 import 不修改）：A 无虚构行 / B 匹配率≥0.6 / C 无重复期 /
  D 期序单调 / E 时效≤5 / F 标注覆盖 / G 底部锚定。ds 读数不稳（同图多次调用读行数不同）→
  G 门软化（底部行没读出不算失败）+ B 门失败自动重试（最多 3 次）。
- **resize 夹逼**：过大下采样到 ≤1024×2200，过小（640 栈图）上采样到 ≥1024 宽，统一 JPEG q90。
- **零污染约束**：filter/resize/judge 只写新文件；`image_patterns_with_blogger.json`（2145 条旧流程产物）、
  `crops_all_manifest.json`、`exclude_list.json` 哈希校验不变；20260829/30 目录不动。
  **⑥画规自证复现** 只写 `guihua_<period>_reads.json` + `.verdict.json`；`judge_<date>.json` 只读不写。
  旧校验器 `verify_patterns_26233.py` / `out_of_sample_hit_26233.py`（服务旧五阶段流程，机器候选核验已降级）保留不删。

---

## 1. 历史管线（五阶段，26230–26232 期使用，已被六步管线取代）

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
| ④ 命中核验 | **多位置逐张重读**：20 条旧命中 → **真命中 18（1位置1中 3 / 多位置部分命中 15）/剔除 2（星辰888 不定位铁码、流萤 无画规）**；规律库定稿 **17**（验证排除 用规说话_4 描述有误，见被剔除清单）。后按**单押口径**复读 26230 → **命中 6**（仅单码必中：熊大出品 万9、微时光 十8、默言言心 万9、二叔×2 万9、小屁股 万9） | `glm_multipos_recheck.json`、`docs/GLM命中重核报告-20260831.md` |
| ⑤ 方法库 | **48 位博主**方法条目（类型/描述/推理/预测/命中），数据驱动初版完成 | `pattern_methods.json`、`docs/画规方法库.md` |
| 规律库 | 26230 期命中规律 **6 条**（单押口径，含各位置对错 + 画规逻辑 + 窗口内验证） | `docs/规律/26230.md` + `26230.json` |
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
├── modules/image_recognize/     # 六步管线（现行，2026-08-31 起）
│   ├── filter_trend.py          # ② 确定性过滤（OpenCV+CNN+OCR，无 LLM）→ filter_report.json
│   ├── crop_all.py              # ③ 裁剪（复用；仅 status==cropped 进识别）
│   ├── resize_crops.py          # ④ 显式 resize（640→≥1024 宽）→ vision/*.jpg
│   ├── judge_accuracy.py        # ⑤ 视觉判定+命中 → judge_<date>.json + 命中图集
│   ├── read_guihua.py           # ⑥ 读整张命中原图画规 → guihua_<period>_reads.json
│   ├── verify_patterns.py       # （旧）机器候选核验，已降级进⑤自检，保留不删
│   └── cv_trend_reader/         # 底层原语（行带/列定位/标注形态/期号OCR/开奖匹配）
├── agents/                    # 原始多Agent框架（collector 已接真实数据）
├── tools/                     # 数据/验证/方法库工具（主战场）
│   ├── crawl_gouli.py         # ① gouli99 论坛图爬虫
│   ├── reproduce_guihua.py    # ⑥ 画规自证复现（值保真+逻辑自洽）
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
