"""
核心数据结构定义 —— 所有 Agent 之间流转的数据都用这些 dataclass
"""
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from enum import Enum


# ============================================================
# 任务定义
# ============================================================
@dataclass
class Task:
    """用户下达的原始任务"""
    task_id: str
    target_site: str               # 目标网站标识
    history_periods: int           # 回溯多少期历史数据
    max_posts_per_period: int      # 每期最多爬多少帖子
    predict_target: str            # 预测目标描述
    extra_config: Dict[str, Any] = field(default_factory=dict)


# ============================================================
# 采集层数据
# ============================================================
@dataclass
class HistoryRecord:
    """一期历史数字记录"""
    period: str            # 期号，如 "2026001"
    numbers: List[int]     # 开奖数字
    date: str              # 日期 YYYY-MM-DD


@dataclass
class Post:
    """一条网友帖子/评论"""
    period: str            # 对应哪一期
    author_id: str         # 发帖人ID
    content: str           # 原始发言文本
    post_time: str = ""    # 发帖时间


# ============================================================
# 分析层数据（熔炉Agent输出）
# ============================================================
@dataclass
class Pattern:
    """一条提炼出来的规律"""
    pattern_id: str                # 规律唯一标识（hash）
    description: str               # 规律描述，如 "连出3次奇数后转偶数"
    source_authors: List[str]      # 哪些网友提出过
    support_count: int             # 支持人数
    global_hits: int = 0           # 历史全局命中次数
    global_misses: int = 0         # 历史全局失败次数
    recent_hits: int = 0           # 最近N期命中次数
    recent_misses: int = 0         # 最近N期失败次数
    evidence: List[str] = field(default_factory=list)  # 用到的历史数据论据

    @property
    def global_accuracy(self) -> float:
        total = self.global_hits + self.global_misses
        return self.global_hits / total if total > 0 else 0.0

    @property
    def recent_accuracy(self) -> float:
        total = self.recent_hits + self.recent_misses
        return self.recent_hits / total if total > 0 else 0.0


@dataclass
class PatternPool:
    """熔炉Agent输出的候选规律池"""
    patterns: List[Pattern] = field(default_factory=list)
    period: str = ""               # 对应哪一期的分析


# ============================================================
# 决策层数据（决策Agent输出）
# ============================================================
class PatternStatus(str, Enum):
    ACTIVE = "active"           # 启用
    DISCARDED = "discarded"     # 丢弃
    OBSERVING = "observing"     # 待观察


@dataclass
class SelectedPattern:
    """决策Agent选中的规律"""
    pattern: Pattern
    weight: float               # 权重 0-1
    score: float                # 综合得分
    status: PatternStatus = PatternStatus.ACTIVE


@dataclass
class DiscardedPattern:
    """被丢弃的规律"""
    pattern: Pattern
    reason: str                 # 丢弃原因


@dataclass
class DecisionResult:
    """决策Agent完整输出"""
    selected: List[SelectedPattern] = field(default_factory=list)
    discarded: List[DiscardedPattern] = field(default_factory=list)
    observing: List[Pattern] = field(default_factory=list)
    period: str = ""


# ============================================================
# 最终预测结果
# ============================================================
@dataclass
class PredictionResult:
    period: str                              # 预测哪一期
    prediction: str                          # 预测结论文本
    selected_patterns: List[SelectedPattern] # 用到的规律
    confidence: float = 0.0                  # 置信度 0-1
    raw_analysis: str = ""                   # 分析过程说明
