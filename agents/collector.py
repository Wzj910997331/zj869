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
import re
import urllib.request
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
    # 历史数据抓取（排列5真实数据）
    # ----------------------------------------------------------
    def _fetch_history(self, task: Task) -> List[HistoryRecord]:
        """
        从 500彩票网 datachart 抓取排列5真实历史开奖数据。

        URL: https://datachart.500.com/plw/history/inc/history.php?limit=N
        - 需浏览器 UA + Referer；响应为 gb2312 编码的 HTML 表格
        - 数据行 <tr class="t_tr1">，列 = [摇奖用球/套, 期号, 号码(5位空格分隔),
          和值, 总销售额, 开奖日期]
        - 一次拉全量（如 730 期）偶发失败，重试即可（GitHub 同源项目验证）
        """
        limit = max(task.history_periods, 1)
        url = f"https://datachart.500.com/plw/history/inc/history.php?limit={limit}"
        req = urllib.request.Request(url, headers={
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
            "Referer": "https://datachart.500.com/plw/history/history.shtml",
        })
        raw = urllib.request.urlopen(req, timeout=30).read().decode("gb2312", errors="replace")

        records: List[HistoryRecord] = []
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", raw, re.S):
            if "t_tr1" not in row:
                continue
            cells = [re.sub(r"<[^>]+>", "", c).strip()
                     for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
            if len(cells) < 6:
                continue
            period, nums_str, date = cells[1], cells[2], cells[5]
            numbers = [int(d) for d in re.findall(r"\d", nums_str)]
            if len(numbers) == 5:
                records.append(HistoryRecord(period=period, numbers=numbers, date=date))

        if not records:
            self.error("500彩票网采集失败：未解析到数据行")
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
