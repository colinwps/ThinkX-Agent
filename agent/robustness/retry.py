"""
重试机制

设计:
- RetryPolicy 是策略对象, 决定"什么错误重试 + 怎么退避"
- retry 是装饰器, 把策略应用到任意函数
- 指数退避 + 抖动 (避免雷击同步重试)
- 区分 retryable / non-retryable: 4xx 参数错重试无用, 5xx/网络错应该重试

借鉴 AWS SDK / tenacity 的 API 风格, 但只实现核心 20%。
"""
from __future__ import annotations

import functools
import random
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, TypeVar

T = TypeVar("T")


class RetryError(Exception):
    """重试耗尽后抛出, 包装最后一次的原因"""

    def __init__(self, attempts: int, last_error: BaseException):
        super().__init__(f"重试 {attempts} 次后仍失败: {last_error!r}")
        self.attempts = attempts
        self.last_error = last_error


@dataclass
class RetryPolicy:
    """
    重试策略

    字段:
    - max_attempts: 最多尝试次数(含首次)。3 = 首次 + 2 次重试
    - base_delay:   首次重试等多久(秒)
    - max_delay:    单次等待上限(秒)
    - exp_base:     退避指数底(默认 2: 1s, 2s, 4s, 8s...)
    - jitter:       是否加抖动(避免雷击)
    - retry_on:     接受 exception 实例, 返回 True 时重试
    - on_retry:     每次决定重试时的回调(便于打日志/写 trace)
    """
    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    exp_base: float = 2.0
    jitter: bool = True

    # 默认: 所有 Exception 都重试。实际使用时通常会传更精细的判断
    retry_on: Callable[[BaseException], bool] = field(
        default_factory=lambda: lambda e: True
    )
    on_retry: Callable[[int, BaseException, float], None] | None = None

    def delay_for(self, attempt: int) -> float:
        """attempt 从 1 开始: 第 attempt 次重试前等多久"""
        delay = self.base_delay * (self.exp_base ** (attempt - 1))
        delay = min(delay, self.max_delay)
        if self.jitter:
            delay *= 0.5 + random.random()  # [0.5x, 1.5x] 抖动
        return delay


def retry(policy: RetryPolicy):
    """
    装饰器版重试。

    用法:
        @retry(RetryPolicy(max_attempts=3, retry_on=is_network_error))
        def call_api():
            ...
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_error: BaseException | None = None

            for attempt in range(1, policy.max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except BaseException as e:
                    last_error = e

                    # 最后一次, 不再重试
                    if attempt >= policy.max_attempts:
                        break

                    # 检查这个错误该不该重试
                    if not policy.retry_on(e):
                        # 不可重试 -> 直接抛出原异常
                        raise

                    # 算退避时间
                    delay = policy.delay_for(attempt)
                    if policy.on_retry:
                        try:
                            policy.on_retry(attempt, e, delay)
                        except Exception:
                            pass  # 回调出错不影响主流程

                    time.sleep(delay)

            assert last_error is not None
            raise RetryError(policy.max_attempts, last_error)

        return wrapper

    return decorator


# ============================================================
# 内置错误判断函数 - LLM API 场景
# ============================================================

def is_retryable_llm_error(error: BaseException) -> bool:
    """
    判断一个 LLM API 错误是否值得重试。

    重试: 5xx / 429 / 网络错误 / 超时
    不重试: 4xx (除 429) / 鉴权 / 参数错误
    """
    # OpenAI SDK 的错误类型(LangChain 之类同款封装也是这套)
    # 这里用 ducktyping, 不强依赖 openai 包
    error_type_name = type(error).__name__
    error_str = str(error).lower()

    # 网络层错误
    if error_type_name in (
        "APIConnectionError", "APITimeoutError",
        "ConnectionError", "Timeout", "TimeoutError",
        "ReadTimeout", "ConnectTimeout", "RemoteDisconnected",
    ):
        return True

    # 不可重试的错误关键字
    non_retryable_keywords = [
        "invalid api key", "incorrect api key", "authentication",
        "permission denied", "bad request", "invalid request",
        "not found", "model not found",
    ]
    for kw in non_retryable_keywords:
        if kw in error_str:
            return False

    # status_code 字段(OpenAI SDK 风格的 APIError)
    status = getattr(error, "status_code", None) or getattr(error, "status", None)
    if isinstance(status, int):
        if status == 429:  # rate limit
            return True
        if 500 <= status < 600:  # server error
            return True
        if 400 <= status < 500:  # client error
            return False

    # 默认: 不知道的错误也重试一次试试(给定 max_attempts 不会无限)
    return True


def default_llm_retry_policy() -> RetryPolicy:
    """LLM 调用的默认重试策略 - 最多 3 次, 1/2/4s 退避"""

    def on_retry(attempt: int, error: BaseException, delay: float):
        print(
            f"[retry] LLM 调用失败 (第 {attempt} 次): {type(error).__name__}: {error}. "
            f"等 {delay:.1f}s 后重试...",
            file=sys.stderr,
        )

    return RetryPolicy(
        max_attempts=3,
        base_delay=1.0,
        max_delay=30.0,
        exp_base=2.0,
        jitter=True,
        retry_on=is_retryable_llm_error,
        on_retry=on_retry,
    )


def is_retryable_mcp_error(error: BaseException) -> bool:
    """
    MCP 调用错误判断。

    重试: stdio 通信中断、超时、连接断开
    不重试: 业务错误 (这些已经被 MCPClient 转成正常返回的文本, 不会进到这里)
            参数校验错误 (validation 之类)
    """
    name = type(error).__name__
    s = str(error).lower()

    # 业务校验错误不重试
    if "validation" in s or "invalid" in s or "schema" in s:
        return False

    # IO/超时/连接类 -> 重试
    retryable_names = {
        "ConnectionError", "BrokenPipeError", "TimeoutError",
        "ConnectionResetError", "ConnectionAbortedError",
        "EOFError", "OSError",
    }
    if name in retryable_names:
        return True

    # MCP SDK 自己的错误类型可能叫 McpError - 看消息判断
    if "mcp" in name.lower():
        # 协议级错误 (比如 server 没启动) 通常网络层, 值得重试
        return True

    # 默认不重试 (业务异常居多)
    return False


def default_mcp_retry_policy() -> RetryPolicy:
    """MCP 调用的默认重试策略 - 比 LLM 短, 因为本地 stdio 没必要等太久"""

    def on_retry(attempt: int, error: BaseException, delay: float):
        print(
            f"[retry] MCP 调用失败 (第 {attempt} 次): {type(error).__name__}: {error}. "
            f"等 {delay:.1f}s 后重试...",
            file=sys.stderr,
        )

    return RetryPolicy(
        max_attempts=3,
        base_delay=0.3,
        max_delay=5.0,
        exp_base=2.0,
        jitter=True,
        retry_on=is_retryable_mcp_error,
        on_retry=on_retry,
    )
