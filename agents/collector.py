"""
采集Agent（对标鹰眼Agent）
职责：
1. 抓取历史数字序列
2. 爬取每一期的网友帖子/评论
3. 清洗脏数据（广告、水帖），输出结构化数据
4. 只负责拿数据，不做解读、不总结规律

[注意] 实际使用时，把 _fetch_history() 和 _fetch_posts() 替换为真实爬虫逻辑
   框架层提供模拟数据，保证流程可跑通
"""
import random
from typing import List, Tuple

from agents.base import BaseAgent
from models.schemas import HistoryRecord, Post, Task
from memory.vcp import VCPMemory


class CollectorAgent(BaseAgent):
    def __init__(self, vcp: VCPMemory):
        super().__init__("Collector(鹰眼)")
        self.vcp = vcp

    def run(self, task: Task) -> Tuple[List[HistoryRecord], List[Post]]:
        """
        输入: Task
        输出: (历史记录列表, 帖子列表)
        """
        self.log(f"开始采集: 站点={task.target_site}, 回溯{task.history_periods}期")

        # 1. 抓取历史数字
        history = self._fetch_history(task)
        self.log(f"采集到历史数据 {len(history)} 期")

        # 2. 抓取帖子
        posts = self._fetch_posts(task, history)
        self.log(f"采集到帖子 {len(posts)} 条")

        # 3. 清洗脏数据
        posts = self._clean_posts(posts)
        self.log(f"清洗后剩余帖子 {len(posts)} 条")

        # 4. 写入VCP记忆库
        self.vcp.save_history(history)
        self.vcp.save_posts(posts)

        return history, posts

    # ----------------------------------------------------------
    # 历史数据抓取（替换为真实爬虫）
    # ----------------------------------------------------------
    def _fetch_history(self, task: Task) -> List[HistoryRecord]:
        """
        TODO: 替换为真实网站爬取逻辑
        示例：生成模拟历史数据
        """
        records = []
        for i in range(task.history_periods):
            period_num = 2026001 + i
            # 模拟：7个数字，范围1-30
            numbers = sorted(random.sample(range(1, 31), 7))
            records.append(HistoryRecord(
                period=str(period_num),
                numbers=numbers,
                date=f"2026-01-{i+1:02d}",
            ))
        return records

    # ----------------------------------------------------------
    # 帖子抓取（替换为真实爬虫）
    # ----------------------------------------------------------
    def _fetch_posts(self, task: Task, history: List[HistoryRecord]) -> List[Post]:
        """
        TODO: 替换为真实论坛/社区爬取逻辑
        示例：为每期生成模拟网友发言
        """
        sample_patterns = [
            "连出奇数后这期必出偶数",
            "尾数7间隔5期必出",
            "大小交替规律，这期该大数",
            "和值连续上升，这期会回落",
            "三区比看2:3:2",
            "冷热号交替，冷号该回补了",
            "跨度在缩小，这期跨度小于15",
            "质数最近偏少，这期补质数",
        ]
        posts = []
        for record in history[-min(len(history), 10):]:  # 只给最近10期生成帖子
            for j in range(random.randint(2, task.max_posts_per_period)):
                pattern = random.choice(sample_patterns)
                posts.append(Post(
                    period=record.period,
                    author_id=f"user_{random.randint(1000, 9999)}",
                    content=f"我觉得{pattern}，你们怎么看？",
                    post_time=f"2026-01-{int(record.period[-3:])+1:02d} 20:30",
                ))
        return posts

    # ----------------------------------------------------------
    # 数据清洗
    # ----------------------------------------------------------
    def _clean_posts(self, posts: List[Post]) -> List[Post]:
        """过滤广告、水帖、无意义回复"""
        blacklist_keywords = ["加微信", "私聊", "代投", "广告", "http", "www."]
        cleaned = []
        for p in posts:
            content = p.content.strip()
            # 过短
            if len(content) < 5:
                continue
            # 黑名单关键词
            if any(kw in content for kw in blacklist_keywords):
                continue
            cleaned.append(p)
        return cleaned
