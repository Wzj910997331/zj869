# image_recognize 独立模块：走势图快速识别 + 规律分析

**完全独立**：不 import 主包（config.py / main.py / models/ / memory/ / agents/ 零接触）。
所有输出写在新增路径 `data/recognize/{blogger}/{date}/` + `docs/图片规律识别报告-{blogger}-{date}.md`。
`git status` 应只出现本模块新文件与新输出目录。

## 方案（2026-08-31 定稿）

用户的原始想法是"图片 → OpenCV 预处理 → 小模型检测+分类 → 大模型规律分析"。
实测后 **CNN 数字识别被放弃**（`train_digits.py` + `model/digit_cnn.py` 仅存历史）：合成字体数据在真实
走势图数字上泛化差，且对 6 张图识别出的数字无法与 lottery 对齐，投入产出比低。

**最终方案 = 裁剪规律区域 + 视觉大模型读数字 + 规则引擎提规律**，直接回应用户
"裁剪他画规律的区域、减少模型输入量来提高速度"：

1. **stage1 网格几何**：OpenCV 检测绿色数字掩码 → 列中心（吸附模板 [348,499,648,798,949]）、
   行带（间距≈136px）、已填充行。
2. **stage2 规律区域裁剪**：博主标注 = **强色块**（纯红/纯蓝，`max-min>80 && max>120` 饱和度掩码）。
   底图（白底/绿数字/浅青行带/灰网格线）全部低饱和 → 自动排除。
   - 只对**已填充行**统计行窗内饱和像素（>200）→ 标注行。
   - 输出：`01_rows.png` 全行栈（640 宽，保留行序+红色行标签）、`02_annotated.png` 标注行栈（快路径）、
     `03_debug.png` 原图画框核对图、`crops_manifest.json`（含每标注行覆盖了哪几个位置列）。
   - 实测 6 图全部正确：图0 标注行 1-10，其余图 3-10 不等；行3 满带万千百十，其余部分位置。
3. **stage4 读数字 + 规律**：
   - **视觉读数字**：标注行栈（fast）或全行栈（full）→ glm-5.3-flash 逐行读出 5 位数，
     与 `lottery_recent.json` 精确匹配**自校正**（row0 用已知最新期锚定）。
     实测 10/10 行 × 5/5 位精确。
   - **规则引擎提候选**：从匹配行确定性提取 斜连/定位/和值/胆码/杀号/头/尾/数字串，按
     支持度 + 博主色带覆盖位置提权排序取 top-3。数字全部来自真实开奖，不臆造。
   - **叙事总结**：deepseek-v4-flash 对 top-3 规律做一句话中文概括（~2s；失败则跳过）。
   - **hit() 校验**：对目标期 draw 判定每条规律命中与否，写 patterns.json + docs 报告。

## 关键实测结论：网关模型是"始终思考"型

`llm.riverbegin.cn` 的 glm-5.3-flash / deepseek-v4-flash **都是 always-thinking 推理模型**：

- 开放式任务（"从彩票数字找规律"、"从候选里挑最优"）会**推理死循环**，max_tokens 全被隐藏
  `reasoning_content` 吃光、返回空 content（试过 12 候选 4000 max_tokens、`reasoning_effort=low`、
  `enable_thinking=false`，全失败）。
- **受限小任务能稳定终止**：glm 视觉读数字（客观转录）、deepseek 一句话概括（~2s）。
- deepseek-v4-flash **无视觉能力**（读图全 null），glm 有。→ 读数字必须用 glm。
- 因此"大模型规律分析"的落点收敛为：规则引擎精确提规律（数字不臆造）+ deepseek 事后一句话解读。

## 用法

```bash
# 全流程（先跑小屁股_483847515 的 6 张图）
/usr/bin/python3 modules/image_recognize/run.py \
    --blogger 小屁股_483847515 --date 2026-08-28

# 断点续跑：只重跑 stage2 及以后
/usr/bin/python3 modules/image_recognize/run.py --blogger 小屁股_483847515 \
    --date 2026-08-28 --from-stage 2

# 单阶段直接调用
/usr/bin/python3 modules/image_recognize/stage4_llm.py \
    --manifest data/recognize/小屁股_483847515/20260828/manifest.json \
    [--model glm-5.3-flash] [--analysis-model deepseek-v4-flash] [--mode full]
```

`--mode fast`（默认）只送博主标注过的行条给视觉模型（输入小、快）；`--mode full` 送全部行
（读得全但慢 ~2×）。参数 `--model` 换视觉模型（需有视觉能力）、`--analysis-model` 换叙事模型。

## 产物

```
data/recognize/{blogger}/{date}/
  manifest.json          输入清单（run_id/目标期/图片/lottery路径/out_dir）
  grid_geometry.json     每图列心/行带/行填充
  crops_manifest.json    裁剪索引（标注行/位置列/两栈路径）
  crops/<img>/01_rows.png        全行栈（读数字全量输入）
  crops/<img>/02_annotated.png   标注行栈（快路径主输入）
  crops/<img>/03_debug.png       原图画框核对
  patterns.json           每图 读数自校正映射 + 色带覆盖 + top-3 规律(hit) + 叙事
docs/图片规律识别报告-{blogger}-{date}.md
```

规律 schema 与主包一致：`{blogger,file,type,position,numbers,desc,img_type,hit}`；
类型 定位/斜连/胆码/头/尾/和值/杀号/数字串/其他；位置 万0/千1/百2/十3/个4。
