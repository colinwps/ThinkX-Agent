"""
上下文管理子系统

提供:
- token_estimator: 不调 API 估算 messages 的 token 数
- compressor: 历史压缩(摘要式)
- budget: token 预算管理 + 自动触发压缩

设计理念:
- "便宜的本地估算 + 关键时刻的远程压缩"
- 估算用 tiktoken (本地, 几乎免费)
- 压缩调一次便宜模型 (deepseek-chat 即可)
- 压缩只重写"中段历史", 保留前缀让 prompt cache 还能命中
"""
from .budget import BudgetManager, default_budget
from .cache_helper import CacheStats, format_cache_savings
from .compressor import HistoryCompressor
from .token_estimator import estimate_tokens, estimate_messages_tokens

__all__ = [
    "BudgetManager", "default_budget",
    "HistoryCompressor",
    "estimate_tokens", "estimate_messages_tokens",
    "CacheStats", "format_cache_savings",
]
