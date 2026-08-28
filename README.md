# zj869 — 多Agent协作框架（数据规律采集与预测）

> 参考亚马逊多Agent选品架构，改造为「历史数据采集 + 网友观点分析 + 规律决策 + 预测」系统

## 架构概览

```
用户任务
   │
   ▼
┌─────────────────────────────────────────────┐
│           HubAgent（枢纽/总调度）              │
│  负责任务拆解、Agent调度、数据流转、报告输出     │
└──────────┬──────────┬──────────┬────────────┘
           │          │          │
     ┌─────▼───┐ ┌───▼────┐ ┌──▼──────────┐
     │Collector│ │Analyzer│ │  Decision    │
     │(鹰眼采集)│ │(熔炉分析)│ │  (规律决策)   │
     └─────┬───┘ └───┬────┘ └──┬───────────┘
           │          │          │
           └──────────┴──────────┘
                      │
               ┌──────▼──────┐
               │  VCP 记忆库  │
               │ 历史/观点/规律 │
               └─────────────┘
```

## 四个Agent分工

| Agent | 职责 | 输入 | 输出 |
|-------|------|------|------|
| **Hub（枢纽）** | 总调度，任务流转，报告输出 | Task | PredictionResult |
| **Collector（鹰眼）** | 爬历史数据+帖子，清洗结构化 | Task | (HistoryRecord[], Post[]) |
| **Analyzer（熔炉）** | 提取规律，合并去重，历史回测 | (history, posts, period) | PatternPool |
| **Decision（决策）** | 黑名单过滤→打分→冲突消解→选TopN | (pattern_pool, history, period) | DecisionResult + Prediction |

## VCP记忆库（三块存储）

1. **原始历史库** — 完整历史数字序列
2. **网友观点库** — 每一期所有人的发言 + 提炼出的规律
3. **规律效果库**
   - `effective_pool` 有效池：历史多次命中
   - `failed_pool` 失效池：长期失效（黑名单，决策时直接过滤）
   - `observing_pool` 待观察池：新规律，样本不足

## 决策Agent核心逻辑

```
候选规律池
    │
    ▼
[步骤1] 黑名单过滤 — 从VCP失效池直接剔除
    │
    ▼
[步骤2] 多维度打分
    ├─ 近期命中率 × 0.5（最重要）
    ├─ 全局命中率 × 0.3
    └─ 社区支持度 × 0.2（参考，不迷信）
    │
    ▼
[步骤3] 冲突消解 — 互斥规律（奇vs偶、大vs小）只保留高分
    │
    ▼
[步骤4] 选Top N + 分配权重（归一化）
    │
    ▼
最终启用规律集合 → 生成预测
```

## 闭环反馈

真实结果公布后，调用 `hub.feedback(period, real_numbers)`：
- 自动评估每条规律是否命中
- 更新VCP库中的命中/失败统计
- 连续失败的规律自动移入失效池
- 系统越跑越准，抑制AI幻觉

## 快速开始

```bash
python main.py
```

## 需要你替换的地方（TODO标记）

| 文件 | 方法 | 说明 |
|------|------|------|
| `agents/collector.py` | `_fetch_history()` | 替换为真实网站历史数据爬虫 |
| `agents/collector.py` | `_fetch_posts()` | 替换为真实论坛/社区帖子爬虫 |
| `agents/analyzer.py` | `_extract_patterns()` | 可接入大模型API做规律提取 |
| `agents/analyzer.py` | `_evaluate_single_pattern()` | 扩展更多规律类型的数值验证 |
| `agents/decision.py` | `_check_hit()` | 扩展命中判断逻辑 |

## 项目结构

```
zj869/
├── main.py                  # 入口示例
├── config.py                # 配置
├── requirements.txt         # 依赖（无外部依赖，纯标准库）
├── README.md
├── agents/
│   ├── base.py              # Agent基类
│   ├── hub.py               # 枢纽Agent
│   ├── collector.py         # 采集Agent（鹰眼）
│   ├── analyzer.py          # 分析Agent（熔炉）
│   └── decision.py          # 决策Agent
├── memory/
│   └── vcp.py               # VCP持久记忆库
├── models/
│   └── schemas.py           # 数据结构定义
└── utils/
    └── logger.py            # 日志工具
```

## 免责声明

本框架仅用于技术架构演示和数据规律研究。彩票类数字属于完全随机事件，历史规律不具备预测效力，请勿用于赌博或任何非法用途。
