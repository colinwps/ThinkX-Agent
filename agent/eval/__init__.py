"""
评估子系统

设计:
- case.py: 一个 "case" = 一组 (input, assertions, metadata)
- assertions.py: 断言, 程序判定 (tool 调用 / 关键词)
- judge.py: LLM-as-judge, 主观打分
- runner.py: 跑一组 case
- store.py: 历史 runs 持久化
- viewer.py: 终端报告
"""
from .case import Case, CaseSuite, load_suite
from .runner import EvalRunner, RunResult, CaseResult
from .store import EvalStore
from .assertions import (
    Assertion, ToolCalled, NoToolCalled,
    Contains, NotContains, LLMJudge,
)

__all__ = [
    "Case", "CaseSuite", "load_suite",
    "EvalRunner", "RunResult", "CaseResult",
    "EvalStore",
    "Assertion", "ToolCalled", "NoToolCalled",
    "Contains", "NotContains", "LLMJudge",
]
