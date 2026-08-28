"""
规律决策Agent（核心新增）
职责：
1. 从VCP失效池加载黑名单，直接过滤
2. 对剩余候选规律多维度打分
   - 近期命中率（权重最高）
   - 全局命中率
   - 社区支持度（参考，不迷信）
3. 冲突检测与消解（互斥规律只保留高分）
4. 输出最终启用规律集合 + 权重 + 丢弃理由
5. 基于选中规律生成预测结论
"""
from typing import List, Tuple, Dict
from collections import defaultdict

from agents.base import BaseAgent
from models.schemas import (
    Pattern,
    PatternPool,
    PatternStatus,
    SelectedPattern,
    DiscardedPattern,
    DecisionResult,
    PredictionResult,
    HistoryRecord,
)
from memory.vcp import VCPMemory


class DecisionAgent(BaseAgent):
    def __init__(self, vcp: VCPMemory, max_selected: int = 5):
        super().__init__("Decision(决策)")
        self.vcp = vcp
        self.max_selected = max_selected  # 最多启用几条规律

        # 打分权重配置
        self.score_weights = {
            "recent_accuracy": 0.5,   # 近期命中率（最重要）
            "global_accuracy": 0.3,   # 全局命中率
            "support_count": 0.2,     # 社区支持度（参考）
        }

    def run(self, input_data: Tuple[PatternPool, List[HistoryRecord], str]) -> DecisionResult:
        """
        输入: (候选规律池, 历史记录, 目标期号)
        输出: DecisionResult
        """
        pool, history, target_period = input_data
        self.log(f"开始决策: 候选规律{len(pool.patterns)}条, 目标期={target_period}")

        # 步骤1: 黑名单过滤
        remaining, discarded_blacklist = self._filter_blacklist(pool.patterns)
        self.log(f"黑名单过滤: 剔除{len(discarded_blacklist)}条, 剩余{len(remaining)}条")

        # 步骤2: 多维度打分
        scored = self._score_patterns(remaining)
        self.log(f"打分完成")

        # 步骤3: 冲突检测与消解
        resolved, discarded_conflict = self._resolve_conflicts(scored)
        self.log(f"冲突消解: 剔除{len(discarded_conflict)}条冲突规律")

        # 步骤4: 取Top N，分配权重
        selected, discarded_low = self._select_top_n(resolved)
        self.log(f"最终选中{len(selected)}条规律")

        # 组装结果
        all_discarded = discarded_blacklist + discarded_conflict + discarded_low
        result = DecisionResult(
            selected=selected,
            discarded=all_discarded,
            observing=[p for p in remaining if p.support_count < 3],  # 小众规律入待观察
            period=target_period,
        )

        # 写入VCP记忆库
        self._persist_to_vcp(result)

        return result

    # ----------------------------------------------------------
    # 步骤1: 黑名单过滤
    # ----------------------------------------------------------
    def _filter_blacklist(self, patterns: List[Pattern]) -> Tuple[List[Pattern], List[DiscardedPattern]]:
        failed = self.vcp.get_failed_patterns()
        failed_ids = {p.pattern_id for p in failed}

        remaining = []
        discarded = []
        for p in patterns:
            if p.pattern_id in failed_ids:
                discarded.append(DiscardedPattern(
                    pattern=p,
                    reason="VCP失效黑名单：历史回测长期失效",
                ))
            else:
                remaining.append(p)
        return remaining, discarded

    # ----------------------------------------------------------
    # 步骤2: 多维度打分
    # ----------------------------------------------------------
    def _score_patterns(self, patterns: List[Pattern]) -> List[Tuple[Pattern, float]]:
        """对每条规律计算综合得分 0-1"""
        if not patterns:
            return []

        # 归一化支持度
        max_support = max(p.support_count for p in patterns) or 1

        scored = []
        for p in patterns:
            score = (
                p.recent_accuracy * self.score_weights["recent_accuracy"]
                + p.global_accuracy * self.score_weights["global_accuracy"]
                + (p.support_count / max_support) * self.score_weights["support_count"]
            )
            scored.append((p, round(score, 4)))

        # 按得分降序
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    # ----------------------------------------------------------
    # 步骤3: 冲突检测与消解
    # ----------------------------------------------------------
    def _resolve_conflicts(self, scored: List[Tuple[Pattern, float]]) -> Tuple[List[Tuple[Pattern, float]], List[DiscardedPattern]]:
        """
        检测互斥规律，只保留得分更高的
        互斥规则示例：
        - 推"奇数" vs 推"偶数"
        - 推"大数" vs 推"小数"
        """
        conflict_pairs = [
            ("奇数", "偶数"),
            ("大数", "小数"),
            ("质数", "合数"),
            ("上升", "回落"),
        ]

        discarded = []
        resolved = []
        discarded_ids = set()

        for i, (p1, s1) in enumerate(scored):
            if p1.pattern_id in discarded_ids:
                continue
            for j, (p2, s2) in enumerate(scored):
                if i >= j or p2.pattern_id in discarded_ids:
                    continue
                # 检测是否互斥
                if self._is_conflict(p1.description, p2.description, conflict_pairs):
                    if s1 >= s2:
                        discarded.append(DiscardedPattern(
                            pattern=p2,
                            reason=f"与规律「{p1.description[:20]}」互斥，得分更低({s2}<{s1})",
                        ))
                        discarded_ids.add(p2.pattern_id)
                    else:
                        discarded.append(DiscardedPattern(
                            pattern=p1,
                            reason=f"与规律「{p2.description[:20]}」互斥，得分更低({s1}<{s2})",
                        ))
                        discarded_ids.add(p1.pattern_id)
                        break
            if p1.pattern_id not in discarded_ids:
                resolved.append((p1, s1))

        return resolved, discarded

    @staticmethod
    def _is_conflict(desc1: str, desc2: str, conflict_pairs: List[Tuple[str, str]]) -> bool:
        for a, b in conflict_pairs:
            if (a in desc1 and b in desc2) or (b in desc1 and a in desc2):
                return True
        return False

    # ----------------------------------------------------------
    # 步骤4: 选Top N + 分配权重
    # ----------------------------------------------------------
    def _select_top_n(self, scored: List[Tuple[Pattern, float]]) -> Tuple[List[SelectedPattern], List[DiscardedPattern]]:
        selected = []
        discarded = []

        top = scored[:self.max_selected]
        total_score = sum(s for _, s in top) or 1.0

        for p, score in top:
            weight = round(score / total_score, 4)
            selected.append(SelectedPattern(
                pattern=p,
                weight=weight,
                score=score,
                status=PatternStatus.ACTIVE,
            ))

        for p, score in scored[self.max_selected:]:
            discarded.append(DiscardedPattern(
                pattern=p,
                reason=f"综合得分排名超出Top{self.max_selected} (得分={score})",
            ))

        return selected, discarded

    # ----------------------------------------------------------
    # 生成最终预测
    # ----------------------------------------------------------
    def generate_prediction(self, decision: DecisionResult,
                             history: List[HistoryRecord]) -> PredictionResult:
        """基于选中的规律生成预测结论"""
        if not decision.selected:
            return PredictionResult(
                period=decision.period,
                prediction="无可靠规律，建议观望",
                selected_patterns=[],
                confidence=0.0,
                raw_analysis="所有候选规律均被过滤，无可用规律",
            )

        # 汇总各规律的预测方向
        directions = []
        for sp in decision.selected:
            direction = self._pattern_to_direction(sp.pattern)
            directions.append(f"[{sp.weight:.0%}] {direction}")

        prediction_text = "；".join(directions)
        confidence = sum(sp.weight * sp.score for sp in decision.selected)

        # 分析过程
        analysis_lines = [
            f"启用{len(decision.selected)}条规律，"
            f"丢弃{len(decision.discarded)}条，"
            f"待观察{len(decision.observing)}条",
        ]
        for sp in decision.selected:
            p = sp.pattern
            analysis_lines.append(
                f"  - {p.description[:30]} | "
                f"近期命中率={p.recent_accuracy:.0%} | "
                f"全局命中率={p.global_accuracy:.0%} | "
                f"支持人数={p.support_count} | "
                f"权重={sp.weight:.0%}"
            )

        return PredictionResult(
            period=decision.period,
            prediction=prediction_text,
            selected_patterns=decision.selected,
            confidence=round(confidence, 4),
            raw_analysis="\n".join(analysis_lines),
        )

    def _pattern_to_direction(self, pattern: Pattern) -> str:
        """把规律描述转化为预测方向文本"""
        desc = pattern.description
        if "奇数" in desc and "偶数" in desc:
            return "倾向出偶数"
        if "大数" in desc:
            return "倾向出大数(>15)"
        if "小数" in desc:
            return "倾向出小数(≤15)"
        if "质数" in desc:
            return "倾向出质数"
        if "和值" in desc and "回落" in desc:
            return "和值倾向回落"
        if "跨度" in desc:
            return "跨度倾向缩小"
        return f"按规律「{desc[:20]}」判断"

    # ----------------------------------------------------------
    # 持久化到VCP
    # ----------------------------------------------------------
    def _persist_to_vcp(self, result: DecisionResult):
        """把决策结果写入VCP记忆库"""
        for sp in result.selected:
            self.vcp.add_pattern(sp.pattern, PatternStatus.ACTIVE)
        for dp in result.discarded:
            # 只有因黑名单/长期失效丢弃的才入失效池
            if "黑名单" in dp.reason or "长期失效" in dp.reason:
                self.vcp.add_pattern(dp.pattern, PatternStatus.DISCARDED)
        for p in result.observing:
            self.vcp.add_pattern(p, PatternStatus.OBSERVING)

    # ----------------------------------------------------------
    # 闭环反馈：真实结果回灌
    # ----------------------------------------------------------
    def evaluate_and_feedback(self, period: str, real_numbers: List[int],
                                prediction: PredictionResult):
        """
        真实结果出来后，评估每条规律是否命中，回灌VCP
        TODO: 根据实际业务扩展命中判断逻辑
        """
        self.log(f"回灌 {period} 期真实结果，评估 {len(prediction.selected_patterns)} 条规律")
        for sp in prediction.selected_patterns:
            # 示例：简单判断（实际应根据规律类型做数值验证）
            hit = self._check_hit(sp.pattern, real_numbers)
            self.vcp.update_pattern_stats(sp.pattern.pattern_id, hit, is_recent=True)

            # 如果连续失败，移入失效池
            if sp.pattern.recent_misses >= 5 and sp.pattern.recent_accuracy < 0.3:
                self.vcp.move_pattern(sp.pattern.pattern_id, "effective_pool", "failed_pool")
                self.log(f"规律连续失败，移入失效池: {sp.pattern.description[:30]}")

    def _check_hit(self, pattern: Pattern, real_numbers: List[int]) -> bool:
        """检查规律是否命中（示例实现）"""
        desc = pattern.description
        last = real_numbers[-1]
        if "偶数" in desc:
            return last % 2 == 0
        if "奇数" in desc and "偶数" not in desc:
            return last % 2 == 1
        if "大数" in desc:
            return last > 15
        if "小数" in desc:
            return last <= 15
        import random
        return random.random() > 0.5
