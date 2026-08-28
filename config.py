"""
配置文件
"""
import os

# 项目根目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# VCP记忆库存储路径
VCP_STORAGE_PATH = os.path.join(BASE_DIR, "vcp_memory.json")

# 决策Agent配置
MAX_SELECTED_PATTERNS = 5        # 最多启用几条规律
RECENT_WINDOW_SIZE = 10           # 近期命中率统计窗口（期数）

# 采集配置
DEFAULT_HISTORY_PERIODS = 30      # 默认回溯多少期
DEFAULT_MAX_POSTS_PER_PERIOD = 5  # 每期最多爬多少帖子

# 打分权重（决策Agent）
SCORE_WEIGHTS = {
    "recent_accuracy": 0.5,   # 近期命中率
    "global_accuracy": 0.3,   # 全局命中率
    "support_count": 0.2,     # 社区支持度
}
