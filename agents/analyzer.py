"""
熔炉分析Agent
职责：
1. 从帖子中提取每个人总结的规律
2. 合并相同规律，统计支持人数
3. 用历史数据做回测，计算每条规律的命中率
4. 输出候选规律池（PatternPool）

[注意] 实际使用时，_extract_patterns_from_post() 可接入大模型API
   框架层用关键词匹配做示例
"""
import re
from typing import List, Dict, Tuple
from collections import defaultdict

from agents.base import BaseAgent
from models.schemas import (
    HistoryRecord,
    Post,
    Pattern,
    PatternPool,
)
from memory.vcp import VCPMemory


class AnalyzerAgent(BaseAgent):
    def __init__(self, vcp: VCPMemory):
        super().__init__("Analyzer(熔炉)")
        self.vcp = vcp

    def run(self, input_data: Tuple[List[HistoryRecord], List[Post], str]) -> PatternPool:
        """
        输入: (历史记录, 帖子列表, 目标预测期号)
        输出: PatternPool 候选规律池
        """
        history, posts, target_period = input_data
        self.log(f"开始分析: {len(posts)}条帖子, 目标期={target_period}")

        # 1. 从帖子提取规律
        raw_patterns = self._extract_patterns(posts)
        self.log(f"提取到原始规律 {len(raw_patterns)} 条")

        # 2. 合并相同规律，统计支持度
        merged = self._merge_patterns(raw_patterns)
        self.log(f"合并后规律 {len(merged)} 条")

        # 3. 历史回测
        backtested = self._backtest_patterns(merged, history)
        self.log(f"回测完成，有效规律 {len(backtested)} 条")

        # 4. 组装输出
        pool = PatternPool(patterns=backtested, period=target_period)
        return pool

    # ----------------------------------------------------------
    # 规律提取（可替换为大模型API）
    # ----------------------------------------------------------
    def _extract_patterns(self, posts: List[Post]) -> List[Dict]:
        """
        从帖子文本中提取规律
        TODO: 实际使用时接入大模型，如：
          prompt = "从以下网友发言中提取他总结的数字规律..."
        示例：用简单的模式匹配提取
        """
        extracted = []
        # 常见规律描述模式
        pattern_keywords = [
            "奇数", "偶数", "大数", "小数", "质数", "合数",
            "尾数", "和值", "跨度", "冷热", "三区", "连号",
            "间隔", "交替", "回补", "上升", "回落",
        ]
        for post in posts:
            content = post.content
            # 简单提取：包含规律关键词的句子
            for kw in pattern_keywords:
                if kw in content:
                    # 提取包含关键词的短句
                    sentences = re.split(r'[，。！？、]', content)
                    for s in sentences:
                        if kw in s and len(s) > 3:
                            extracted.append({
                                "description": s.strip(),
                                "author": post.author_id,
                                "period": post.period,
                            })
                            break
        return extracted

    # ----------------------------------------------------------
    # 合并相同规律
    # ----------------------------------------------------------
    def _merge_patterns(self, raw: List[Dict]) -> List[Pattern]:
        """合并描述相似的规律，统计支持人数"""
        groups: Dict[str, List[Dict]] = defaultdict(list)
        for item in raw:
            # 简单合并：用描述前10个字作为key（实际可用语义相似度）
            key = item["description"][:10]
            groups[key].append(item)

        patterns = []
        for key, items in groups.items():
            desc = items[0]["description"]
            authors = list(set(it["author"] for it in items))
            periods = list(set(it["period"] for it in items))
            patterns.append(Pattern(
                pattern_id=VCPMemory.gen_pattern_id(desc),
                description=desc,
                source_authors=authors,
                support_count=len(authors),
                evidence=[f"来自{len(periods)}期讨论"],
            ))
        return patterns

    # ----------------------------------------------------------
    # 历史回测
    # ----------------------------------------------------------
    def _backtest_patterns(self, patterns: List[Pattern],
                            history: List[HistoryRecord]) -> List[Pattern]:
        """
        用历史数据回测每条规律的命中率
        TODO: 实际使用时，根据规律描述编写对应的数值验证逻辑
        示例：对"奇数/偶数"类规律做简单回测
        """
        if len(history) < 3:
            self.warn("历史数据不足，跳过后测")
            return patterns

        for p in patterns:
            hits = 0
            misses = 0
            recent_hits = 0
            recent_misses = 0
            recent_window = min(10, len(history) - 1)

            for i in range(1, len(history)):
                prev = history[i - 1]
                curr = history[i]
                is_recent = i >= len(history) - recent_window

                # 示例回测逻辑：根据规律描述关键词做简单判断
                hit = self._evaluate_single_pattern(p.description, prev, curr)

                if hit:
                    hits += 1
                    if is_recent:
                        recent_hits += 1
                else:
                    misses += 1
                    if is_recent:
                        recent_misses += 1

            p.global_hits = hits
            p.global_misses = misses
            p.recent_hits = recent_hits
            p.recent_misses = recent_misses

        return patterns

    def _evaluate_single_pattern(self, desc: str,
                                   prev: HistoryRecord,
                                   curr: HistoryRecord) -> bool:
        """
        单条规律的数值验证（示例实现）
        TODO: 根据实际业务扩展更多规律类型的验证逻辑
        """
        prev_last = prev.numbers[-1]
        curr_last = curr.numbers[-1]

        # 奇数转偶数
        if "奇数" in desc and "偶数" in desc:
            return prev_last % 2 == 1 and curr_last % 2 == 0
        # 大数（>15）
        if "大数" in desc:
            return curr_last > 15
        # 小数（<=15）
        if "小数" in desc:
            return curr_last <= 15
        # 质数
        if "质数" in desc:
            return self._is_prime(curr_last)
        # 默认：随机（实际应扩展）
        import random
        return random.random() > 0.5

    @staticmethod
    def _is_prime(n: int) -> bool:
        if n < 2:
            return False
        for i in range(2, int(n**0.5) + 1):
            if n % i == 0:
                return False
        return True
