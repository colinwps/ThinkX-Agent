"""
脱敏(redaction)

设计:
- Redactor 是策略对象, 接受任意 Python 对象返回脱敏后的拷贝
- 默认实现: 关闭(完整记录) / 标记词替换 / 正则规则
- Tracer 在写 payload 前会过一遍 Redactor
- 用户可以自定义 Redactor(实现 redact 方法即可)

为什么这么设计:
线上接业务后(比如停车业务的车牌、手机号), 你会突然发现 trace 里全是 PII。
现在就把口子留好, 不至于到时候推倒重来。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable


# 类型: 一个脱敏函数 接受任意值, 返回脱敏后的值
RedactFn = Callable[[Any], Any]


class Redactor:
    """脱敏器基类: 默认啥也不做"""

    def redact(self, value: Any) -> Any:
        return value


class NoopRedactor(Redactor):
    """显式的"什么都不做"实现"""
    pass


@dataclass
class RegexRedactor(Redactor):
    """
    基于正则的脱敏器。

    示例:
        RegexRedactor.with_common_patterns()    # 启用一组内置规则
        RegexRedactor(rules=[
            (r"\\b1[3-9]\\d{9}\\b", "[手机号]"),       # 中国手机号
            (r"sk-[A-Za-z0-9]{20,}", "[API_KEY]"),    # API key
        ])
    """
    rules: list[tuple[str, str]] = field(default_factory=list)
    _compiled: list[tuple[re.Pattern, str]] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self):
        self._compiled = [(re.compile(p), repl) for p, repl in self.rules]

    @classmethod
    def with_common_patterns(cls) -> "RegexRedactor":
        """一组开箱即用的常见敏感模式"""
        return cls(rules=[
            # API keys
            (r"sk-[A-Za-z0-9\-_]{16,}", "[REDACTED_API_KEY]"),
            (r"Bearer\s+[A-Za-z0-9\-_\.]{16,}", "Bearer [REDACTED_TOKEN]"),
            # 邮箱
            (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "[REDACTED_EMAIL]"),
            # 中国手机号
            (r"\b1[3-9]\d{9}\b", "[REDACTED_PHONE]"),
            # 中国身份证(简化, 不严格)
            (r"\b[1-9]\d{5}(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b",
             "[REDACTED_ID_CARD]"),
            # 银行卡(13-19位连续数字)
            (r"\b\d{13,19}\b", "[REDACTED_CARD_NUMBER]"),
        ])

    def redact(self, value: Any) -> Any:
        return self._walk(value)

    def _walk(self, v: Any) -> Any:
        if isinstance(v, str):
            return self._redact_string(v)
        if isinstance(v, dict):
            return {k: self._walk(val) for k, val in v.items()}
        if isinstance(v, (list, tuple)):
            return type(v)(self._walk(x) for x in v)
        return v

    def _redact_string(self, s: str) -> str:
        for pattern, repl in self._compiled:
            s = pattern.sub(repl, s)
        return s


@dataclass
class TruncateRedactor(Redactor):
    """
    长度截断脱敏器: 对超长字段截断, 防止 trace 库爆炸。
    通常和其他 Redactor 组合使用(用 ChainRedactor)。
    """
    max_string_len: int = 10000

    def redact(self, value: Any) -> Any:
        return self._walk(value)

    def _walk(self, v: Any) -> Any:
        if isinstance(v, str) and len(v) > self.max_string_len:
            return v[: self.max_string_len] + f"...[truncated {len(v) - self.max_string_len} chars]"
        if isinstance(v, dict):
            return {k: self._walk(val) for k, val in v.items()}
        if isinstance(v, (list, tuple)):
            return type(v)(self._walk(x) for x in v)
        return v


@dataclass
class ChainRedactor(Redactor):
    """按顺序应用多个 redactor"""
    redactors: Iterable[Redactor] = field(default_factory=list)

    def redact(self, value: Any) -> Any:
        for r in self.redactors:
            value = r.redact(value)
        return value
