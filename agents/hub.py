"""
枢纽Agent（总调度）
职责：
1. 接收用户任务
2. 按顺序调度其他Agent：采集 -> 分析 -> 决策 -> 预测
3. 在Agent之间传递数据
4. 读写VCP记忆库
5. 输出最终报告
6. 提供闭环反馈接口（真实结果回灌）

不做任何主观推理，只做调度、合并、格式输出
"""
import uuid
from typing import Optional

from agents.base import BaseAgent
from agents.collector import CollectorAgent
from agents.analyzer import AnalyzerAgent
from agents.decision import DecisionAgent
from models.schemas import Task, PredictionResult
from memory.vcp import VCPMemory


class HubAgent(BaseAgent):
    def __init__(self, vcp: VCPMemory, max_selected_patterns: int = 5):
        super().__init__("Hub(枢纽)")
        self.vcp = vcp

        # 初始化子Agent
        self.collector = CollectorAgent(vcp)
        self.analyzer = AnalyzerAgent(vcp)
        self.decision = DecisionAgent(vcp, max_selected=max_selected_patterns)

        # 保存最近一次预测结果（用于闭环反馈）
        self._last_prediction: Optional[PredictionResult] = None

    def run(self, task: Task) -> PredictionResult:
        """
        主流程：采集 -> 分析 -> 决策 -> 预测
        """
        self.log("=" * 60)
        self.log(f"任务启动: {task.task_id}")
        self.log(f"目标站点: {task.target_site} | 回溯{task.history_periods}期")
        self.log("=" * 60)

        # ---------- 阶段1: 采集 ----------
        self.log("\n[阶段1/4] 采集Agent启动")
        history, posts = self.collector.run(task)
        if not history:
            self.error("采集失败：无历史数据")
            return PredictionResult(
                period="unknown", prediction="采集失败，无历史数据",
                selected_patterns=[], confidence=0.0,
            )

        # 确定目标预测期号（最后一期+1）
        last_period_num = int(history[-1].period)
        target_period = str(last_period_num + 1)
        self.log(f"目标预测期: {target_period}")

        # ---------- 阶段2: 分析（熔炉） ----------
        self.log("\n[阶段2/4] 熔炉分析Agent启动")
        pattern_pool = self.analyzer.run((history, posts, target_period))
        self.log(f"候选规律池: {len(pattern_pool.patterns)}条")

        # ---------- 阶段3: 决策 ----------
        self.log("\n[阶段3/4] 规律决策Agent启动")
        decision_result = self.decision.run((pattern_pool, history, target_period))

        # ---------- 阶段4: 生成预测 ----------
        self.log("\n[阶段4/4] 生成最终预测")
        prediction = self.decision.generate_prediction(decision_result, history)
        self._last_prediction = prediction

        # ---------- 输出报告 ----------
        self._print_report(prediction, decision_result)

        self.log("\n" + "=" * 60)
        self.log("任务完成")
        self.log("=" * 60)

        return prediction

    # ----------------------------------------------------------
    # 闭环反馈
    # ----------------------------------------------------------
    def feedback(self, period: str, real_numbers: list):
        """
        真实结果出来后，回灌更新规律命中率
        Args:
            period: 期号
            real_numbers: 真实开奖数字列表
        """
        if not self._last_prediction:
            self.warn("无最近预测记录，跳过反馈")
            return
        if self._last_prediction.period != period:
            self.warn(f"期号不匹配: 预测期={self._last_prediction.period}, 实际期={period}")
            return

        self.log(f"闭环反馈: {period}期真实结果={real_numbers}")
        self.decision.evaluate_and_feedback(period, real_numbers, self._last_prediction)
        self.log("反馈完成，规律命中率已更新")

    # ----------------------------------------------------------
    # 报告输出
    # ----------------------------------------------------------
    def _print_report(self, prediction: PredictionResult, decision_result):
        print("\n" + "=" * 60)
        print(f"  预测报告 — 第 {prediction.period} 期")
        print("=" * 60)
        print(f"\n【预测结论】")
        print(f"  {prediction.prediction}")
        print(f"\n【置信度】 {prediction.confidence:.1%}")

        print(f"\n【启用规律】({len(decision_result.selected)}条)")
        for i, sp in enumerate(decision_result.selected, 1):
            p = sp.pattern
            print(f"  {i}. {p.description}")
            print(f"     权重={sp.weight:.0%} | 得分={sp.score:.3f} | "
                  f"近期命中={p.recent_accuracy:.0%} | 全局命中={p.global_accuracy:.0%} | "
                  f"支持={p.support_count}人")

        if decision_result.discarded:
            print(f"\n【丢弃规律】({len(decision_result.discarded)}条)")
            for dp in decision_result.discarded[:5]:  # 最多显示5条
                print(f"  × {dp.pattern.description[:30]}... — {dp.reason}")
            if len(decision_result.discarded) > 5:
                print(f"  ... 还有 {len(decision_result.discarded) - 5} 条")

        if decision_result.observing:
            print(f"\n【待观察】({len(decision_result.observing)}条)")
            for p in decision_result.observing[:3]:
                print(f"  ○ {p.description[:30]}... (支持{p.support_count}人，样本不足)")

        print("\n" + "=" * 60)
