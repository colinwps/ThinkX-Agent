"""
稳健性子系统

提供四类能力:
- guards:  工具结果护栏(长度限制 + 命令黑名单)
- timeout: 工具执行超时
- retry:   带退避的重试装饰器
- llm_client: 给 OpenAI client 加上重试外壳

设计理念:
所有功能都"非侵入"—— 通过装饰器/包装器组合上去, 不动核心代码。
"""
from .guards import (
    CommandValidator,
    DangerousCommandError,
    ToolResultGuard,
    guard_tool,
)
from .llm_client import ResilientOpenAI
from .retry import (
    RetryError,
    RetryPolicy,
    default_llm_retry_policy,
    default_mcp_retry_policy,
    is_retryable_llm_error,
    is_retryable_mcp_error,
    retry,
)
from .timeout import TimeoutError, with_timeout

__all__ = [
    "ToolResultGuard", "guard_tool",
    "CommandValidator", "DangerousCommandError",
    "with_timeout", "TimeoutError",
    "retry", "RetryPolicy", "RetryError",
    "default_llm_retry_policy", "default_mcp_retry_policy",
    "is_retryable_llm_error", "is_retryable_mcp_error",
    "ResilientOpenAI",
]
