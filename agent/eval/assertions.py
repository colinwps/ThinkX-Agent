"""
断言: 判断一次 Agent run 是否符合预期

设计:
- Assertion 是接口: 接收 RunContext (含工具调用历史、最终输出), 返回 (passed, message)
- 几种内置:
  - ToolCalled: 检查某工具被调用过(可选检查参数)
  - NoToolCalled: 检查某工具没被调用
  - Contains: 最终输出含某关键词
  - NotContains: 最终输出不含某关键词
  - LLMJudge: 用 LLM 评分(在 judge.py 里, 这里只占位 dataclass)

设计哲学:
- 程序判断 > LLM 判断 (更稳定、便宜、可复现)
- 只在"程序判断不了"的地方用 LLM (语义、风格)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class RunContext:
    """一次 Agent run 的执行轨迹, 给 assertion 评估用"""
    user_input: str
    final_output: str
    tool_calls: list[dict] = field(default_factory=list)  # [{name, arguments, result, approved}]
    iterations: int = 0
    duration_seconds: float = 0.0
    error: str | None = None

    def tool_names(self) -> list[str]:
        return [tc["name"] for tc in self.tool_calls]


@dataclass
class AssertionResult:
    passed: bool
    message: str = ""
    # 给 LLM judge 用: 0-10 分; 程序断言只有 0 或 10
    score: float = 0.0


class Assertion(Protocol):
    """所有断言的接口"""
    def evaluate(self, ctx: RunContext) -> AssertionResult: ...

    @property
    def label(self) -> str:
        """用于报告: 简短描述"""
        ...


# ============================================================
# 工具调用断言
# ============================================================

@dataclass
class ToolCalled:
    """断言某工具被调用了。可选: 检查参数包含的字段"""
    name: str
    args_contain: dict[str, Any] | None = None  # 部分参数匹配
    args_equal: dict[str, Any] | None = None    # 完全匹配

    @property
    def label(self) -> str:
        if self.args_equal is not None:
            return f"调用 {self.name}({self.args_equal})"
        if self.args_contain is not None:
            return f"调用 {self.name}(含 {self.args_contain})"
        return f"调用 {self.name}"

    def evaluate(self, ctx: RunContext) -> AssertionResult:
        matching = [tc for tc in ctx.tool_calls if tc["name"] == self.name]
        if not matching:
            return AssertionResult(False, f"{self.name} 未被调用 (实际调用: {ctx.tool_names()})")

        if self.args_equal is not None:
            for tc in matching:
                if tc["arguments"] == self.args_equal:
                    return AssertionResult(True, "ok", score=10.0)
            return AssertionResult(
                False,
                f"{self.name} 参数不匹配: 期望 {self.args_equal}, "
                f"实际 {[m['arguments'] for m in matching]}"
            )

        if self.args_contain is not None:
            for tc in matching:
                args = tc["arguments"]
                if all(self._field_match(args.get(k), v) for k, v in self.args_contain.items()):
                    return AssertionResult(True, "ok", score=10.0)
            return AssertionResult(
                False,
                f"{self.name} 没有调用包含 {self.args_contain}, "
                f"实际 {[m['arguments'] for m in matching]}"
            )

        return AssertionResult(True, "ok", score=10.0)

    @staticmethod
    def _field_match(actual, expected) -> bool:
        """单个字段匹配: 字符串子串, 其他完全相等"""
        if isinstance(expected, str) and isinstance(actual, str):
            return expected.lower() in actual.lower()
        return actual == expected


@dataclass
class NoToolCalled:
    """断言某工具不应被调用"""
    name: str

    @property
    def label(self) -> str:
        return f"不调用 {self.name}"

    def evaluate(self, ctx: RunContext) -> AssertionResult:
        if self.name in ctx.tool_names():
            return AssertionResult(False, f"{self.name} 不应被调用但被调用了")
        return AssertionResult(True, "ok", score=10.0)


@dataclass
class ToolCallCount:
    """断言工具调用总次数在某范围"""
    min: int = 0
    max: int = 999

    @property
    def label(self) -> str:
        return f"工具调用次数 {self.min}-{self.max}"

    def evaluate(self, ctx: RunContext) -> AssertionResult:
        n = len(ctx.tool_calls)
        if n < self.min:
            return AssertionResult(False, f"工具调用次数 {n} < {self.min}")
        if n > self.max:
            return AssertionResult(False, f"工具调用次数 {n} > {self.max}")
        return AssertionResult(True, f"{n} 次", score=10.0)


# ============================================================
# 输出内容断言
# ============================================================

@dataclass
class Contains:
    """断言最终输出含某关键词。case_sensitive 默认 False"""
    text: str
    case_sensitive: bool = False

    @property
    def label(self) -> str:
        return f"输出含 \"{self.text}\""

    def evaluate(self, ctx: RunContext) -> AssertionResult:
        haystack = ctx.final_output
        needle = self.text
        if not self.case_sensitive:
            haystack = haystack.lower()
            needle = needle.lower()
        if needle in haystack:
            return AssertionResult(True, "ok", score=10.0)
        return AssertionResult(
            False,
            f"输出不含 \"{self.text}\"。实际输出: {ctx.final_output[:100]}..."
        )


@dataclass
class NotContains:
    """断言最终输出不含某关键词"""
    text: str
    case_sensitive: bool = False

    @property
    def label(self) -> str:
        return f"输出不含 \"{self.text}\""

    def evaluate(self, ctx: RunContext) -> AssertionResult:
        haystack = ctx.final_output
        needle = self.text
        if not self.case_sensitive:
            haystack = haystack.lower()
            needle = needle.lower()
        if needle in haystack:
            return AssertionResult(
                False, f"输出不应含 \"{self.text}\" 但含了"
            )
        return AssertionResult(True, "ok", score=10.0)


@dataclass
class ContainsAny:
    """断言输出含给定关键词中的至少一个"""
    options: list[str]
    case_sensitive: bool = False

    @property
    def label(self) -> str:
        return f"输出含 {self.options} 中至少一个"

    def evaluate(self, ctx: RunContext) -> AssertionResult:
        haystack = ctx.final_output
        if not self.case_sensitive:
            haystack = haystack.lower()
        for opt in self.options:
            if (opt if self.case_sensitive else opt.lower()) in haystack:
                return AssertionResult(True, f"命中 {opt}", score=10.0)
        return AssertionResult(False, f"输出不含 {self.options} 中任何一个")


# ============================================================
# LLM-as-Judge (占位 dataclass, 实现在 judge.py)
# ============================================================

@dataclass
class LLMJudge:
    """用 LLM 评分: criteria 描述判断标准, threshold 通过分数线"""
    criteria: str
    threshold: float = 7.0  # 分数 >= threshold 算 pass
    # judge.evaluate 时会注入一个 LLM call 函数

    @property
    def label(self) -> str:
        return f"LLM judge: {self.criteria[:40]}{'...' if len(self.criteria) > 40 else ''}"

    def evaluate(self, ctx: RunContext) -> AssertionResult:
        # 没注入 judge_fn 时直接告诉用户
        return AssertionResult(
            False,
            "LLMJudge 未配置 judge_fn (需要在 EvalRunner 里装上)"
        )


# ============================================================
# 从 YAML dict 构造
# ============================================================

ASSERTION_BUILDERS = {}


def register_builder(type_name: str):
    def deco(fn):
        ASSERTION_BUILDERS[type_name] = fn
        return fn
    return deco


@register_builder("tool_called")
def _build_tool_called(d: dict) -> Assertion:
    return ToolCalled(
        name=d["name"],
        args_contain=d.get("args_contain") or d.get("args"),
        args_equal=d.get("args_equal"),
    )


@register_builder("no_tool_called")
def _build_no_tool_called(d: dict) -> Assertion:
    return NoToolCalled(name=d["name"])


@register_builder("tool_call_count")
def _build_count(d: dict) -> Assertion:
    return ToolCallCount(min=d.get("min", 0), max=d.get("max", 999))


@register_builder("contains")
def _build_contains(d: dict) -> Assertion:
    return Contains(text=d["text"], case_sensitive=d.get("case_sensitive", False))


@register_builder("not_contains")
def _build_not_contains(d: dict) -> Assertion:
    return NotContains(text=d["text"], case_sensitive=d.get("case_sensitive", False))


@register_builder("contains_any")
def _build_contains_any(d: dict) -> Assertion:
    return ContainsAny(options=d["options"], case_sensitive=d.get("case_sensitive", False))


@register_builder("llm_judge")
def _build_llm_judge(d: dict) -> Assertion:
    return LLMJudge(criteria=d["criteria"], threshold=d.get("threshold", 7.0))


def build_assertion(d: dict) -> Assertion:
    """从 YAML 单条 assert dict 构造 Assertion 对象"""
    t = d.get("type")
    if t not in ASSERTION_BUILDERS:
        raise ValueError(f"未知 assertion type: {t}, 支持: {list(ASSERTION_BUILDERS.keys())}")
    return ASSERTION_BUILDERS[t](d)
