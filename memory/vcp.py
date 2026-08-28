"""
VCP 持久记忆库
三块存储：
1. 原始历史库 (history)      —— 完整历史数字序列
2. 网友观点库 (posts)         —— 每一期所有人的发言 + 提炼出的规律
3. 规律效果库 (patterns)
   - effective_pool  有效池：历史多次命中
   - failed_pool     失效池：长期失效（黑名单）
   - observing_pool  待观察池：新规律，样本不足

用 JSON 文件持久化，简单直接，后续可替换为 SQLite/向量库
"""
import json
import os
import hashlib
from typing import List, Dict, Optional
from datetime import datetime

from models.schemas import (
    HistoryRecord,
    Post,
    Pattern,
    PatternStatus,
)
from utils.logger import get_logger

logger = get_logger("VCP")


class VCPMemory:
    def __init__(self, storage_path: str = "vcp_memory.json"):
        self.storage_path = storage_path
        self._data = self._load()

    # ----------------------------------------------------------
    # 持久化
    # ----------------------------------------------------------
    def _load(self) -> Dict:
        if os.path.exists(self.storage_path):
            try:
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"记忆库加载失败，重新初始化: {e}")
        return {
            "history": [],
            "posts": [],
            "patterns": {
                "effective_pool": [],
                "failed_pool": [],
                "observing_pool": [],
            },
            "meta": {"created_at": datetime.now().isoformat(), "version": "1.0"},
        }

    def _save(self):
        try:
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except IOError as e:
            logger.error(f"记忆库保存失败: {e}")

    # ----------------------------------------------------------
    # 1. 原始历史库
    # ----------------------------------------------------------
    def save_history(self, records: List[HistoryRecord]):
        """批量保存历史记录，去重（按期号）"""
        existing = {r["period"] for r in self._data["history"]}
        new_count = 0
        for r in records:
            if r.period not in existing:
                self._data["history"].append({
                    "period": r.period,
                    "numbers": r.numbers,
                    "date": r.date,
                })
                new_count += 1
        if new_count:
            logger.info(f"历史库新增 {new_count} 期记录")
            self._save()

    def get_history(self, limit: Optional[int] = None) -> List[HistoryRecord]:
        records = [
            HistoryRecord(period=r["period"], numbers=r["numbers"], date=r["date"])
            for r in self._data["history"]
        ]
        if limit:
            return records[-limit:]
        return records

    # ----------------------------------------------------------
    # 2. 网友观点库
    # ----------------------------------------------------------
    def save_posts(self, posts: List[Post]):
        """保存帖子，去重（author_id + period + content hash）"""
        existing = {
            (p["period"], p["author_id"], p["content"][:50])
            for p in self._data["posts"]
        }
        new_count = 0
        for p in posts:
            key = (p.period, p.author_id, p.content[:50])
            if key not in existing:
                self._data["posts"].append({
                    "period": p.period,
                    "author_id": p.author_id,
                    "content": p.content,
                    "post_time": p.post_time,
                })
                new_count += 1
        if new_count:
            logger.info(f"观点库新增 {new_count} 条帖子")
            self._save()

    def get_posts_by_period(self, period: str) -> List[Post]:
        return [
            Post(period=p["period"], author_id=p["author_id"],
                 content=p["content"], post_time=p.get("post_time", ""))
            for p in self._data["posts"] if p["period"] == period
        ]

    # ----------------------------------------------------------
    # 3. 规律效果库
    # ----------------------------------------------------------
    @staticmethod
    def gen_pattern_id(description: str) -> str:
        """根据规律描述生成稳定ID"""
        return hashlib.md5(description.strip().encode("utf-8")).hexdigest()[:12]

    def _pattern_to_dict(self, p: Pattern) -> Dict:
        return {
            "pattern_id": p.pattern_id,
            "description": p.description,
            "source_authors": p.source_authors,
            "support_count": p.support_count,
            "global_hits": p.global_hits,
            "global_misses": p.global_misses,
            "recent_hits": p.recent_hits,
            "recent_misses": p.recent_misses,
            "evidence": p.evidence,
        }

    def _dict_to_pattern(self, d: Dict) -> Pattern:
        return Pattern(
            pattern_id=d["pattern_id"],
            description=d["description"],
            source_authors=d.get("source_authors", []),
            support_count=d.get("support_count", 0),
            global_hits=d.get("global_hits", 0),
            global_misses=d.get("global_misses", 0),
            recent_hits=d.get("recent_hits", 0),
            recent_misses=d.get("recent_misses", 0),
            evidence=d.get("evidence", []),
        )

    def add_pattern(self, pattern: Pattern, pool: PatternStatus = PatternStatus.OBSERVING):
        """新增规律到指定池"""
        pool_key = {
            PatternStatus.ACTIVE: "effective_pool",
            PatternStatus.OBSERVING: "observing_pool",
            PatternStatus.DISCARDED: "failed_pool",
        }[pool]

        # 检查是否已存在
        for existing in self._data["patterns"][pool_key]:
            if existing["pattern_id"] == pattern.pattern_id:
                logger.debug(f"规律已存在于 {pool_key}: {pattern.description[:30]}")
                return

        self._data["patterns"][pool_key].append(self._pattern_to_dict(pattern))
        logger.info(f"规律入库 [{pool_key}]: {pattern.description[:40]}")
        self._save()

    def move_pattern(self, pattern_id: str, from_pool: str, to_pool: str):
        """在池之间移动规律"""
        for i, p in enumerate(self._data["patterns"][from_pool]):
            if p["pattern_id"] == pattern_id:
                self._data["patterns"][to_pool].append(p)
                del self._data["patterns"][from_pool][i]
                logger.info(f"规律移动: {from_pool} -> {to_pool} ({p['description'][:30]})")
                self._save()
                return
        logger.warning(f"未找到规律 {pattern_id} 在 {from_pool}")

    def get_failed_patterns(self) -> List[Pattern]:
        """获取失效黑名单"""
        return [self._dict_to_pattern(p) for p in self._data["patterns"]["failed_pool"]]

    def get_effective_patterns(self) -> List[Pattern]:
        return [self._dict_to_pattern(p) for p in self._data["patterns"]["effective_pool"]]

    def get_observing_patterns(self) -> List[Pattern]:
        return [self._dict_to_pattern(p) for p in self._data["patterns"]["observing_pool"]]

    def update_pattern_stats(self, pattern_id: str, hit: bool, is_recent: bool = False):
        """更新规律命中/失败统计（闭环反馈）"""
        for pool_key in ["effective_pool", "failed_pool", "observing_pool"]:
            for p in self._data["patterns"][pool_key]:
                if p["pattern_id"] == pattern_id:
                    if hit:
                        p["global_hits"] += 1
                        if is_recent:
                            p["recent_hits"] += 1
                    else:
                        p["global_misses"] += 1
                        if is_recent:
                            p["recent_misses"] += 1
                    self._save()
                    logger.info(
                        f"规律统计更新 [{pool_key}]: {p['description'][:30]} "
                        f"hit={hit} (全局 {p['global_hits']}/{p['global_hits']+p['global_misses']})"
                    )
                    return

    # ----------------------------------------------------------
    # 闭环：真实结果回灌
    # ----------------------------------------------------------
    def feedback_real_result(self, period: str, real_numbers: List[int],
                             predicted_patterns: List[Pattern]):
        """
        真实结果出来后，回灌更新所有相关规律的命中率
        predicted_patterns: 本次预测用到的规律列表
        """
        # 这里需要业务逻辑判断每条规律是否命中
        # 框架层只提供接口，具体命中判断由业务层实现
        logger.info(f"收到 {period} 期真实结果回灌，涉及 {len(predicted_patterns)} 条规律")
        # 具体命中判断逻辑在 DecisionAgent.evaluate_pattern_hit() 中实现
