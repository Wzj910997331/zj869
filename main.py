"""
多Agent协作框架 — 入口示例
运行: python main.py
"""
import uuid

from config import (
    VCP_STORAGE_PATH,
    MAX_SELECTED_PATTERNS,
    DEFAULT_HISTORY_PERIODS,
    DEFAULT_MAX_POSTS_PER_PERIOD,
)
from memory import VCPMemory
from agents import HubAgent
from models.schemas import Task


def main():
    # 1. 初始化VCP记忆库
    vcp = VCPMemory(storage_path=VCP_STORAGE_PATH)

    # 2. 初始化枢纽Agent（自动创建所有子Agent）
    hub = HubAgent(vcp, max_selected_patterns=MAX_SELECTED_PATTERNS)

    # 3. 创建任务
    task = Task(
        task_id=str(uuid.uuid4())[:8],
        target_site="example_lottery_forum",
        history_periods=DEFAULT_HISTORY_PERIODS,
        max_posts_per_period=DEFAULT_MAX_POSTS_PER_PERIOD,
        predict_target="下一期数字规律",
    )

    # 4. 执行主流程
    prediction = hub.run(task)

    # 5. （可选）模拟真实结果回灌 — 闭环反馈
    # 实际使用时，等真实结果公布后调用：
    # hub.feedback(period="2026031", real_numbers=[3, 7, 12, 18, 22, 25, 29])

    print("\n[提示]")
    print("  - 真实结果公布后，调用 hub.feedback(period, real_numbers) 回灌更新规律命中率")
    print("  - 替换 collector.py 中的 _fetch_history() 和 _fetch_posts() 为真实爬虫")
    print("  - 替换 analyzer.py 中的 _extract_patterns() 可接入大模型API做规律提取")
    print("  - VCP记忆库数据保存在 vcp_memory.json，系统越跑越准")


if __name__ == "__main__":
    main()
