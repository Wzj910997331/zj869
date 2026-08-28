# 多Agent开发指南（zj869 多Agent协作框架）

> 本文档说明：多Agent 是什么、本框架怎么实现的、按什么顺序开发、用什么工具开发。
> 配合 `README.md`（架构概览）阅读。

---

## 1. 多Agent 的本质

多Agent 系统 = **多个有明确职责的 Agent，通过「编排 + 共享记忆」协作**，完成单个 Agent 难以完成的任务。

核心价值：
- **任务分解**：大任务切成可独立开发、独立测试的阶段
- **可插拔**：每个 Agent 的输入输出契约固定，内部实现可随时替换（模拟→真实爬虫→大模型）
- **记忆积累**：共享记忆库让系统"越跑越准"，而不是每次从零开始
- **可评估**：每阶段有明确输入输出，可以单独回测验证

判断是否该用多Agent：任务是否能分解成多阶段/多视角、是否需要长期记忆、是否需要多数据源。

## 2. 三种协作模式（本框架是组合使用）

| 模式 | 说明 | 在本框架的位置 |
|---|---|---|
| **流水线 Pipeline** | 串行阶段，前一阶段输出 = 后一阶段输入 | Collector → Analyzer → Decision |
| **编排 Orchestration** | 中心调度者：拆任务、调度、异常处理、汇总输出 | `agents/hub.py` 的 HubAgent |
| **黑板/共享记忆 Blackboard** | 所有 Agent 读写同一份共享状态 | `memory/vcp.py` 的 VCP 记忆库 |

## 3. 当前框架的实现映射（代码位置）

| 概念 | 实现文件 | 职责 |
|---|---|---|
| Agent 基类 | `agents/base.py` | `BaseAgent`：统一的 log/warn/error、Agent 名 |
| 编排者 | `agents/hub.py` | `HubAgent.run()`：串起 采集→分析→决策→预测 四阶段，输出报告，提供 `feedback()` 闭环接口 |
| 采集 Agent | `agents/collector.py` | 抓历史数据 + 帖子，清洗结构化（`_fetch_history/_fetch_posts` 现为模拟数据） |
| 分析 Agent | `agents/analyzer.py` | 帖子→提炼规律→合并去重→历史回测算命中率（现为关键词匹配） |
| 决策 Agent | `agents/decision.py` | 黑名单过滤→多维打分→冲突消解→选TopN→生成预测 |
| 数据契约 | `models/schemas.py` | Task / HistoryRecord / Post / Pattern / PatternPool / DecisionResult / PredictionResult |
| 记忆库 | `memory/vcp.py` | `VCPMemory`：原始历史库 / 网友观点库 / 规律效果库（有效池/失效池/观察池），JSON 持久化 |
| 配置 | `config.py` | 打分权重、窗口期数、采集规模、记忆库路径 |

**完整数据流**：

```
Task
 └→ Hub.run()
     ├→ Collector.run() → (history[], posts[]) ──写入──→ VCP.save_history/save_posts
     ├→ Analyzer.run() → PatternPool(候选规律池)
     ├→ Decision.run() → DecisionResult(选中/丢弃/待观察)
     ├→ Decision.generate_prediction() → PredictionResult
     └→ Hub 输出预测报告
真实结果公布后：Hub.feedback(period, real_numbers) → 更新命中率 → 失效规律进黑名单（闭环）
```

## 4. 开发主线：按什么顺序实现

按依赖关系排优先级（每步都可独立验证）：

- **M1 真实数据接入**（改 `collector.py`）
  - `_fetch_history()`：换成真实网站/API 的历史数据爬虫
  - `_fetch_posts()`：换成真实论坛/社区评论爬虫
  - 验证：`history`/`posts` 数据真实、字段完整
- **M2 智能分析**（改 `analyzer.py`）
  - `_extract_patterns()`：接入大模型 API（Claude / DeepSeek / OpenAI）从帖子提炼规律，替换关键词匹配
  - `_evaluate_single_pattern()`：扩展更多规律类型的数值验证逻辑
  - 验证：提炼的规律可解释、回测命中率有意义
- **M3 决策强化**（改 `decision.py` + `config.py`）
  - 按业务调优打分权重、黑名单规则、冲突消解策略
  - 把 `feedback()` 闭环真正跑起来（真实结果回灌）
  - 验证：连续多期回测，观察命中率变化
- **M4 扩展 Agent**（派生自 `base.py`，挂进 `hub.py` 流程）
  - 例如：数据质检 Agent（采集后校验）、批判 Agent（对决策结果反向审查）、预测 Agent（多方法融合）
- **M5 工程化**
  - 单元测试、日志、失败重试、采集限速/并发、配置外置

**每个 Agent 的开发三件套**：
1. 输入契约：明确用哪个 schema（如 `Task`、`(history, posts, period)`）
2. 核心逻辑：替换 TODO 标记处
3. 输出契约 + 验证：跑通 `main.py` 或写个小脚本确认输出符合预期

## 5. 开发方式（工具层）

### 方式A：容器内 Claude Code（推荐做功能实现）
容器 `zhenjie` 内已装 Claude Code（`/usr/local/bin/claude`，v2.1.224）：

```bash
docker exec -it zhenjie bash
cd /data/zhenjie/zj869
claude
```

- 用自然语言让它实现某个 TODO，例如：
  - "把 collector.py 的 _fetch_history 改成爬 https://xxx 的真实历史数据"
  - "给 analyzer.py 加一个大模型规律提取实现，模型走 DeepSeek API"
- Claude Code 会读代码、改文件、自己跑验证
- 建议在对话里引用本文档和 README，保持架构一致

### 方式B：DSH 多智能体（本会话）
- 我可以把开发拆给多个**子代理并行**开发（一个写采集、一个写分析、一个写决策），最后我来集成评审
- 或按 workflow 分阶段推进，每个阶段独立验收

### 方式C：混合协作（推荐）
- **架构/集成/评审**：DSH（本会话）负责
- **单点功能实现**：容器内 Claude Code 负责
- 两者通过 git 同步（以服务器仓库 `/data/zhenjie/zj869` 为主，推送 GitHub）

## 6. 下一步待定

开工前需要明确**具体业务目标**：
1. 采集什么数据？（哪个网站/平台、什么字段）
2. 预测什么？（规律预测的目标：期号/数字/选品/行情？）
3. 数据来源是否可控？（公开接口 / 爬虫 / 已有数据文件）
4. 大模型 API 用哪家？（容器外网通，可接 Claude / DeepSeek / OpenAI）

目标确定后，按 M1→M5 排出本期要做的范围即可开工。
