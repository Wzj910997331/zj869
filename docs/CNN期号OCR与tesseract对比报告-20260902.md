# digit_cnn 期号 OCR vs tesseract — filter_trend 全量 A/B（20260902）

> 目标（Step 1）：用本地 digit_cnn 识别走势图期号列，替代 tesseract 子进程 OCR。
> 判定标准：CNN 能读出 **连续若干期、对应期号正确匹配开奖历史** → filter_trend 的
> period_pairs / period_confidence（≥2 匹配期靠近 target）据此判"走势图"。
> 本报告 = 20260831 全量 583 图，`OCR_ENGINE=tesseract`（基线）vs `OCR_ENGINE=cnn` 决策 A/B，
> 及 §8 **方案A（CNN 主路 + tesseract stale 边界复核）落地全量结果**。
> 本次会话无视觉模型，核验靠 ASCII 字符画 + 引擎间互证，未做像素级人工目检。

---

## 1. 结论（一句话）

**CNN 尚不能安全地**默认替换 **tesseract**：单字精度 ~79% 会让"尾位数字错 1 位"把
近期走势图推过 ±5 期窗口 → **不可逆地误排除**（20260831 确认 ≥2 张近期带标注走势图被误剔）；
但它带来 **~50-80× 加速**，并在 tesseract 整图读不出的图上**恢复出连续期号锚定**
（≥2 期匹配图 46→115 张）。推荐 **混合方案**：CNN 做主路快读，凡 CNN 判定
`stale-period`/读不出锚定的图回退 tesseract 复核后再决定是否排除。

---

## 2. 数据与设置

| 项 | 值 |
|---|---|
| 评估日 / 目标期 | 20260831 / 26233，窗口 ±5 |
| 图数 | 583（images 白名单过滤后）|
| 基线（tesseract） | `data/crawl/20260831/filter_report.json`（02:42，tesseract 特征分布：匹配行 205 行/146 图）|
| CNN 侧 | `OCR_ENGINE=cnn` 重跑 filter_trend → `filter_report_cnn.json`（54.2s）|
| 对比脚本 | `tools/compare_ocr_engines.py` |
| 模型 | digit_cnn（94k，32×48），merge3 1929 real-cell 预增强训练，best val **0.7285** @ep50 |

训练留出集每类精度（原始 cell，20260831 全 583 图重训后）：
0:0.77 1:0.59 2:0.78 3:0.85 4:0.53 5:0.73 6:0.71 7:0.78 **8:0.48** 9:0.60
→ 弱类是 8/4/1/9，正与"尾位 33→22、32→12"这类低位混淆对应。

---

## 3. CNN 期号读取端到端指标（对照 tesseract 已匹配行的 GT，205 行）

| 指标 | CNN |
|---|---|
| 切分失败（读不出 4-6 位） | 35/205 = **17.1%** |
| 串级命中 | 77/205 = **37.6%** |
| 位级精度 | 668/842 = **79.3%** |

> 采样警示：本表只在"tesseract 读对的行"上测——tesseract 全读不出的图上 CNN 反而常能读出
> （见 §4 恢复面），因此该表是 CNN 精度的**下界视图**，不是全量召回率。

---

## 4. 全量 filter_trend A/B（583 图）

### 决策分布
```
tesseract: keep-high 28 / keep-med 106 / uncertain 285 / exclude 164
CNN      : keep-high 45 / keep-med  88 / uncertain 274 / exclude 176
净保留量  134 → 133（几乎不变），keep-high +17（28→45），exclude +12
```
决策变化的图 **137/583 = 23.5%**；period_conf 变化 166 张。

### 每图匹配期号行数分布（≥2 = period_confidence 'high' 的门槛）
```
tesseract: 0:437  1:100  2:35   3:10   5:1      → ≥2 共 46 图
CNN      : 0:424  1:44   2:54   3:28   4:20   5:5  6:8   → ≥2 共 115 图
```

### 恢复面（CNN 读出 tesseract 读不出的连续期）
45 张 CNN keep_high 中绝大多数匹配期是**干净的连续近期串**（26233/26232/26231/…递减），
且常见于 tesseract 整图 matched=[] 的图上（例：同一博主 38c0ffda 的 5 张、
57569423 的 4 张，CNN 全部读出 26233+26232 连续）。逐行独立预测不可能凭空编出跨行递减连续期
→ 这些是**真实恢复**，不是幻觉。tesseract 对它们只能"period-weak → 送视觉"。

### 误排除面（CNN 把近期图推出窗口 → 不可逆 exclude）
CNN 新增 exclude 12 张（10 uncertain→exclude + **2 keep-med→exclude**）。两张 keep-med→exclude
是最危险案例——tesseract 在同一行读到**窗口内**的真实期号，CNN 把尾位读错 1 位推到旧期：

| 图 | tesseract 读 | CNN 读 | CNN 结论 | 危害 |
|---|---|---|---|---|
| bcc9fbba_3 | row1798=`26233`(✓) | `6222`→`26222` | stale-period exclude | 11 行标注图整张丢弃 |
| d2efe7ee_1 | row1492=`26232`(✓) | `26212` | stale-period exclude | 12 行标注图整张丢弃 |

根因：3↔2、3↔1 低位混淆在 ~20% 单字错误率下必然零星出现；5 位期号只要**个/十位错 1 位**
（26233→26222 = −11 期），锚点就跳出 ±5 窗口 → `stale-period` 排除。exclude 是**不可逆**的
（下游 judge 不再看它），故这是替换引擎的硬伤。ASCII 复核支持 tesseract 读法
（bcc9fbba_3 首字为 '2' 形，CNN 丢失前导 2 且把尾 33 读成 22）。
另外 CNN stale-period 由 12→26，多出的部分含 10 张 uncertain→exclude（如博主 37f9ead5 的 5 张，
CNN 读出 26222-26217 干净连续——这批可能是真旧图，CNN 判 stale 反而**省视觉**，需视觉终判）。

---

## 5. 加速比

```
tesseract 基线（20260830 口径）: 356 图 2611s  ≈ 0.14 张/s ≈ 7.4s/张
CNN（20260831）               : 583 图  54.2s ≈ 10.8 张/s ≈ 0.093s/张
→ 单张 ~80×；剔除子进程 spawn（期号 OCR 0.78-3.83s/图序列化开销）后不再受并发打满拖累。
```

---

## 6. 遗留风险

1. **auto 默认陷阱**：`reader.py` 的 `OCR_ENGINE` 默认 `auto`＝有模型即 CNN。
   `digit_cnn.pt` 现在存在且无任何入口显式设 OCR_ENGINE → **下一次生产 run（如 26234）
   会静默切到 CNN**，带上 §4 误排除缺陷。合并前必须显式 `OCR_ENGINE=tesseract` 或落地混合方案。
2. **单字 79% 不够支撑"连续 5 期全对"**：即使 90% 单字，5 位串命中约 59%，仍不稳。
3. 训练集弱类（1/4/8/9）需补更干净的 cell；切分对紧列距图会读到结果列（6 位串 '262212'）。

---

## 7. 建议的落地方式（混合，非替换）

- **CNN 主路快读**：锚定、连续期恢复、keep 侧建议 → 白拿 ~50-80×。
- **tesseract 复核兜底**：只对 CNN 判 `stale-period`（或锚定失败/6 位粘连）的 ~26-40 张/期
  跑一次单行 tesseract（成本可忽略），tesseract 若读到窗口内期号则**不排除**、降级送视觉。
- **或策略保守化**：CNN 读出的期不做 `exclude/stale` 依据，stale 一律 `uncertain → 送视觉`
  （省的是视觉成本，exclude 不该由 CNN 单读决定）。
- 待续：把误排除行回补训练集提单字精度，再谈纯 CNN。

---

## 8. 方案A 已落地：CNN 主路 + tesseract stale 边界复核（20260902 全量）

按 §7 建议实现并全量跑通（`filter_trend.py` + `reader.ocr_digits(engine=)`），
产物 `data/crawl/20260831/filter_report_hybrid.json`（67.3s / 583 图 ≈ 8.7 张/s）。

### 引擎接线（避免 auto 静默陷阱 + 兜底退化）

- **主路固定 CNN**：`OCR_ENGINE` 未显式设置时 filter 主路解析为 `cnn`（auto/hybrid/cnn 同义）；
  显式 `OCR_ENGINE=tesseract` 才回退 tesseract 主路（A/B 基线复现）。
- **tesseract 只在 stale-period 边界复核**（`period_verify_tesseract`）。
  ⚠️ 实施中发现陷阱：reader 的 `auto` 引擎是"CNN 读不出→tesseract 逐行兜底"。
  若主路用 auto，CNN 读不出的 ~424 张会静默退化成旧 tesseract 全成本
  （每个底行 ×6 组合起子进程；实测首版 583 张跑 >13min 未完成）。
  → 主路必须显式 cnn 不回退，复核再显式 tesseract。

### 全量三方对比（583 图）

| 决策 | tesseract 基线 | 纯 CNN | **混合（方案A）** |
|---|---|---|---|
| keep-high | 28 | 45 | **45**（CNN 恢复面保留）|
| keep-med | 106 | 88 | **90**（误剔 2 张救回）|
| uncertain | 285 | 274 | **286** |
| exclude 合计 | 164 | 176 | **162** |
| ├ no-chart | 80 | 80 | 80 |
| ├ anno-trivial | 55 | 53 | 53 |
| ├ no-anno | 17 | 17 | 17 |
| └ **stale-period** | **12** | **26** | **12** |

### stale 复核裁判（触发 26 张）

| 裁判 | 张数 | 处置 | 语义 |
|---|---|---|---|
| refutes-stale | 2 | keep-med | tesseract 读到窗口内期号 → CNN 尾位读错证伪（bcc9fbba_3 `26233`、d2efe7ee_1 `26232`）|
| confirms-stale | 12 | exclude | tesseract 也读到匹配期但全在窗外 → 真旧图，排除（= 基线 12 张）|
| unreadable | 12 | uncertain/period-weak | tesseract 读不出 → 无法证实旧图，**不排除**送视觉 |

### 结论

方案A 拿到 CNN 的全部收益、tesseract 的零误排除：
- **0 张新增 exclude**（基线里 keep/uncertain 无一流向 exclude）；还救回 2 张基线误剔
  （bcc9fbba_3 / d2efe7ee_1，各 11-12 行标注的近期图）。
- 反而 **2 张基线 exclude/anno-trivial 被救回**（CNN 读到 tesseract 读不出的期号
  afef0a46 `26211/26212`、da7d2b43 `26218` → uncertain 送视觉）。
- keep-high 28→45 保留（CNN 恢复连续期锚定的唯一价值不动摇）。
- **耗时 67.3s ≈ 纯 CNN 54.2s（+13s = 26 张复核）**，较 tesseract 全量（~7.4s/张量级）
  **≈ 60× 加速**，stale 排除的"省视觉"能力（12 张真旧图不送视觉）保留。
- 每次周期复核仅 ~26 张 → tesseract 子进程成本可忽略；auto 静默切 CNN 的缺陷随本方案消除
  （auto 主路现在就是 CNN + 边界复核，可安全作默认）。

---

## 产物

- `data/crawl/20260831/filter_report_cnn.json`（CNN 全量决策报告）
- `data/crawl/20260831/filter_report_hybrid.json`（**方案A 混合全量决策报告，67.3s**）
- `/tmp/eval_cnn_20260831.log`（CNN 行级评估）
- `/tmp/compare_cnn_20260831.log`（决策差异明细）
- 训练：`modules/image_recognize/train_digits_preaug.py`，模型 `model/digit_cnn.pt`（best 0.7285）
- 代码改动：`filter_trend.py`（主路 cnn + `period_verify_tesseract` 复核）、
  `reader.py`（`ocr_digits(engine=)`、`cnn_available()`）
