"""
Eval Runner: 跑一组 case + 收集结果

工作流:
- 每个 case 起一个新 Session (不污染历史)
- agent.run_stream() 跑到 Done
- 收集所有 ToolCallStart/Result + 最终输出 -> RunContext
- 跑所有 assertions -> CaseResult
- 全部跑完 -> RunResult

为什么不用 agent.run() (同步阻塞接口):
- 我们要捕获工具调用细节, run_stream 给到事件流更精确
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from ..core import (
    Agent,
    Aborted,
    Done,
    ToolCallResult,
    ToolCallStart,
)
from ..session import Session
from .assertions import (
    Assertion,
    AssertionResult,
    LLMJudge,
    RunContext,
    build_assertion,
)
from .case import Case, CaseSuite
from .judge import evaluate_llm_judge


@dataclass
class AssertionRunResult:
    """一个 assertion 的执行结果(包含其 label)"""
    label: str
    type: str
    passed: bool
    message: str
    score: float = 0.0


@dataclass
class CaseResult:
    """一个 case 的完整结果"""
    case_id: str
    case_input: str
    passed: bool                      # 所有 assert 都过才算 passed
    assertions: list[AssertionRunResult] = field(default_factory=list)
    duration_seconds: float = 0.0
    iterations: int = 0
    tool_calls: list[dict] = field(default_factory=list)
    final_output: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0
    error: str | None = None

    @property
    def pass_count(self) -> int:
        return sum(1 for a in self.assertions if a.passed)

    @property
    def total_assertions(self) -> int:
        return len(self.assertions)


@dataclass
class RunResult:
    """整组 run 的结果"""
    run_id: str
    suite_name: str
    started_at: float
    finished_at: float = 0.0
    cases: list[CaseResult] = field(default_factory=list)

    # 运行配置元数据 (便于 A/B 对比)
    config: dict = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return self.finished_at - self.started_at

    @property
    def pass_count(self) -> int:
        return sum(1 for c in self.cases if c.passed)

    @property
    def total_count(self) -> int:
        return len(self.cases)

    @property
    def pass_rate(self) -> float:
        return self.pass_count / max(1, self.total_count)

    @property
    def total_cost_usd(self) -> float:
        return sum(c.cost_usd for c in self.cases)

    @property
    def total_tokens(self) -> int:
        return sum(c.prompt_tokens + c.completion_tokens for c in self.cases)


class EvalRunner:
    """
    跑一组 case。

    用法:
        runner = EvalRunner(
            agent=agent,
            judge_fn=make_judge_fn(client, "deepseek-chat"),  # 可选: 为了 llm_judge
        )
        result = runner.run_suite(suite)
    """

    def __init__(
        self,
        agent: Agent,
        judge_fn: Callable | None = None,
        config: dict | None = None,
    ):
        self.agent = agent
        self.judge_fn = judge_fn
        self.config = config or {}

    def run_suite(self, suite: CaseSuite, on_case_done: Callable | None = None) -> RunResult:
        """跑完一组 case"""
        import uuid
        run = RunResult(
            run_id=uuid.uuid4().hex[:12],
            suite_name=suite.name,
            started_at=time.time(),
            config={
                **self.config,
                "model": self.agent.model,
                "plan_first": self.agent.plan_first,
                "reflect": self.agent.reflect,
                "parallel_tools": self.agent.parallel_tools,
                "suite_source": suite.source,
            },
        )

        for case in suite.cases:
            cr = self.run_case(case)
            run.cases.append(cr)
            if on_case_done:
                try:
                    on_case_done(case, cr)
                except Exception:
                    pass  # 回调出错不影响主流程

        run.finished_at = time.time()
        return run

    def run_case(self, case: Case) -> CaseResult:
        """跑单个 case"""
        result = CaseResult(case_id=case.id, case_input=case.input, passed=False)

        # 新建 session 隔离
        session = Session(system_prompt=case.system_prompt or "")
        if case.max_iterations is not None:
            saved_max = self.agent.max_iterations
            self.agent.max_iterations = case.max_iterations
        else:
            saved_max = None

        # 收集事件
        tool_calls_by_id: dict[str, dict] = {}
        text_buf: list[str] = []
        final_output = ""
        iterations = 0

        t0 = time.time()
        try:
            for event in self.agent.run_stream(session, case.input):
                if isinstance(event, ToolCallStart):
                    tool_calls_by_id[event.call_id] = {
                        "id": event.call_id,
                        "name": event.name,
                        "arguments": event.arguments,
                        "result": None,
                        "approved": True,
                    }
                elif isinstance(event, ToolCallResult):
                    if event.call_id in tool_calls_by_id:
                        tool_calls_by_id[event.call_id]["result"] = event.result
                        tool_calls_by_id[event.call_id]["approved"] = event.approved
                elif isinstance(event, Done):
                    final_output = event.final_text
                elif isinstance(event, Aborted):
                    result.error = f"aborted: {event.reason}"
                # text/iteration 等略

            # 累计统计
            from ..core import IterationStart
            # 用 session.usage 拿最终统计
            result.prompt_tokens = session.usage.prompt_tokens
            result.completion_tokens = session.usage.completion_tokens
            result.cached_tokens = session.usage.cached_tokens

        except Exception as e:
            result.error = f"{type(e).__name__}: {e}"
        finally:
            if saved_max is not None:
                self.agent.max_iterations = saved_max

        duration = time.time() - t0

        # 算成本
        from ..observability.pricing import estimate_cost
        cost = estimate_cost(
            self.agent.model,
            result.prompt_tokens, result.completion_tokens,
            result.cached_tokens,
        )

        # 整理 RunContext
        tool_calls_list = list(tool_calls_by_id.values())
        ctx = RunContext(
            user_input=case.input,
            final_output=final_output,
            tool_calls=tool_calls_list,
            iterations=session.usage.calls,
            duration_seconds=duration,
            error=result.error,
        )

        result.final_output = final_output
        result.tool_calls = tool_calls_list
        result.duration_seconds = duration
        result.iterations = session.usage.calls
        result.cost_usd = cost

        # 跑 assertions
        all_passed = result.error is None  # 有 error 直接 fail
        for raw_assert in case.asserts:
            try:
                assertion = build_assertion(raw_assert)
            except Exception as e:
                result.assertions.append(AssertionRunResult(
                    label=str(raw_assert), type=str(raw_assert.get("type", "?")),
                    passed=False, message=f"构造失败: {e}",
                ))
                all_passed = False
                continue

            # LLM judge 单独处理
            if isinstance(assertion, LLMJudge):
                if self.judge_fn is None:
                    ar = AssertionResult(False, "未配置 judge_fn")
                else:
                    ar = evaluate_llm_judge(assertion, ctx, self.judge_fn)
            else:
                ar = assertion.evaluate(ctx)

            result.assertions.append(AssertionRunResult(
                label=assertion.label,
                type=raw_assert.get("type", "?"),
                passed=ar.passed, message=ar.message, score=ar.score,
            ))
            if not ar.passed:
                all_passed = False

        result.passed = all_passed
        return result
