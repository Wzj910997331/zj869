"""
Agent 基类
所有 Agent 继承此类，统一接口：
- name: Agent 名称
- run(input_data): 执行任务，返回结构化结果
- 每个 Agent 只做一件事，不越权
"""
from abc import ABC, abstractmethod
from typing import Any, Optional

from utils.logger import get_logger


class BaseAgent(ABC):
    def __init__(self, name: str):
        self.name = name
        self.logger = get_logger(f"Agent:{name}")

    @abstractmethod
    def run(self, input_data: Any) -> Any:
        """
        执行Agent任务
        Args:
            input_data: 结构化输入数据
        Returns:
            结构化输出数据
        """
        pass

    def log(self, msg: str):
        self.logger.info(msg)

    def warn(self, msg: str):
        self.logger.warning(msg)

    def error(self, msg: str):
        self.logger.error(msg)
