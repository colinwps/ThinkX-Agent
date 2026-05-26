"""
ResilientOpenAI: 给 OpenAI client 包一层重试外壳

设计取舍:
- 不继承 / 不 monkey patch OpenAI 类
- 通过组合: 包一个 OpenAI 实例, 暴露 chat.completions.create
- 内部用 retry 装饰创建函数
- 流式响应也支持: 第一次 chunk 拿到后认为"连接建立成功", 后续 chunk 中途断开不重试
  (流式中途断重试需要恢复上下文, 复杂度高, 学习项目跳过)

为什么不直接用 OpenAI SDK 自带的 retry:
- 它的策略不能自定义日志
- 不能用我们的 trace 系统观察重试事件
- 学一遍重试机制更值得
"""
from __future__ import annotations

from typing import Iterator

from openai import OpenAI

from .retry import RetryPolicy, default_llm_retry_policy, retry


class ResilientOpenAI:
    """
    包装 OpenAI client, 给 chat.completions.create 加上重试。

    用法:
        client = ResilientOpenAI(
            api_key=..., base_url=...,
            retry_policy=default_llm_retry_policy(),
        )
        # 接口完全和原 OpenAI 客户端一样
        client.chat.completions.create(...)
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        retry_policy: RetryPolicy | None = None,
        **kwargs,
    ):
        self._inner = OpenAI(api_key=api_key, base_url=base_url, **kwargs)
        self._policy = retry_policy or default_llm_retry_policy()
        self.chat = _ResilientChat(self._inner, self._policy)

    # 透传其他属性, 让它"看起来像" OpenAI 客户端
    def __getattr__(self, name):
        return getattr(self._inner, name)


class _ResilientChat:
    def __init__(self, inner: OpenAI, policy: RetryPolicy):
        self.completions = _ResilientCompletions(inner.chat.completions, policy)


class _ResilientCompletions:
    def __init__(self, inner, policy: RetryPolicy):
        self._inner = inner
        self._policy = policy
        # 把 inner.create 用 retry 装饰
        self._retrying_create = retry(policy)(inner.create)

    def create(self, **kwargs):
        """完全兼容 openai 的 chat.completions.create 签名"""
        return self._retrying_create(**kwargs)
