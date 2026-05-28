"""
example 15: 评估系统演示

这个 example 不需要真 LLM, 用 mock 演示评估流程的几个关键能力:
- 断言系统
- Runner 跑 case
- Store 持久化 + A/B 对比

跑真 cases 用 eval_cli.py:
    python eval_cli.py run evals/library/basic_queries.yaml
"""
import os
import tempfile
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

console = Console()


def demo_assertions():
    """演示各种断言"""
    console.rule("[bold]1. 断言系统[/]")

    from agent.eval.assertions import (
        ToolCalled, NoToolCalled, Contains, NotContains, ContainsAny,
        ToolCallCount, RunContext,
    )

    # 模拟一次 Agent 跑完后的 context
    ctx = RunContext(
        user_input="查《三体》",
        final_output="找到了《三体》, 作者刘慈欣, 库存 4/5",
        tool_calls=[
            {"name": "search_books", "arguments": {"query": "三体"},
             "result": "找到 3 本", "approved": True}
        ],
        iterations=2,
        duration_seconds=1.5,
    )

    cases = [
        ("ToolCalled('search_books')", ToolCalled(name="search_books")),
        ("ToolCalled('search_books', args_contain={'query':'三体'})",
         ToolCalled(name="search_books", args_contain={"query": "三体"})),
        ("ToolCalled('xxx') (应失败)", ToolCalled(name="xxx")),
        ("NoToolCalled('xxx')", NoToolCalled(name="xxx")),
        ("Contains('刘慈欣')", Contains(text="刘慈欣")),
        ("Contains('黑暗森林') (应失败)", Contains(text="黑暗森林")),
        ("ContainsAny(['刘慈欣', '韩松'])", ContainsAny(options=["刘慈欣", "韩松"])),
        ("NotContains('错误')", NotContains(text="错误")),
        ("ToolCallCount(min=1, max=3)", ToolCallCount(min=1, max=3)),
    ]
    for desc, a in cases:
        r = a.evaluate(ctx)
        icon = "[green]✓[/]" if r.passed else "[red]✗[/]"
        console.print(f"  {icon} {desc}")
        if not r.passed:
            console.print(f"      [dim]→ {r.message}[/]")


def demo_runner_with_mock():
    """用 mock LLM 跑一组 case, 演示 Runner + 报告"""
    console.rule("[bold]2. Runner 跑 case (mock LLM)[/]")

    os.environ["DEEPSEEK_API_KEY"] = "fake-for-mock"
    from agent import Agent
    from agent.eval.case import Case, CaseSuite
    from agent.eval.runner import EvalRunner
    from agent.eval.viewer import show_run_summary
    from agent.tools.base import tool
    from agent.tools.registry import ToolRegistry

    # 准备 mock agent
    @tool
    def search_books(query: str) -> str:
        '''查图书'''
        return f"找到《{query}》, 作者刘慈欣, 库存 4"

    @tool
    def find_reader(card_no: str) -> str:
        '''查读者'''
        return f"读者 {card_no}: 张三, 信用分 100"

    reg = ToolRegistry()
    reg.register(search_books)
    reg.register(find_reader)

    # mock LLM 调用 — 不同输入返回不同 tool_call
    from dataclasses import dataclass, field
    from typing import Optional, List
    @dataclass
    class FFunc: name: Optional[str]=None; arguments: Optional[str]=None
    @dataclass
    class FTC: index: int=0; id: Optional[str]=None; type: Optional[str]=None; function: Optional[FFunc]=None
    @dataclass
    class FDelta: content: Optional[str]=None; tool_calls: Optional[List[FTC]]=None
    @dataclass
    class FChoice: delta: FDelta=field(default_factory=FDelta); finish_reason: Optional[str]=None
    @dataclass
    class FChunk: choices: List[FChoice]=field(default_factory=list); usage: Optional[object]=None
    class FakeUsage:
        prompt_tokens = 80
        completion_tokens = 30
        prompt_tokens_details = None

    def make_streamed_response(content="", tool_call=None, finish="stop"):
        chunks = []
        if tool_call:
            chunks.append(FChunk(choices=[FChoice(delta=FDelta(tool_calls=[tool_call]))]))
        if content:
            chunks.append(FChunk(choices=[FChoice(delta=FDelta(content=content))]))
        chunks.append(FChunk(choices=[FChoice(delta=FDelta(), finish_reason=finish)]))
        chunks.append(FChunk(choices=[], usage=FakeUsage()))
        return iter(chunks)

    call_counter = [0]
    def fake_create(**kw):
        call_counter[0] += 1
        messages = kw.get("messages", [])
        last_user = next(
            (m["content"] for m in reversed(messages) if m.get("role") == "user"),
            ""
        )

        if call_counter[0] % 2 == 1:
            # 奇数次: 模拟工具调用
            if "三体" in last_user:
                tc = FTC(index=0, id="c1", type="function",
                         function=FFunc(name="search_books",
                                       arguments='{"query":"三体"}'))
                return make_streamed_response(tool_call=tc, finish="tool_calls")
            elif "R001" in last_user or "张三" in last_user:
                tc = FTC(index=0, id="c1", type="function",
                         function=FFunc(name="find_reader",
                                       arguments='{"card_no":"R001"}'))
                return make_streamed_response(tool_call=tc, finish="tool_calls")
            else:
                return make_streamed_response(content="不知道")
        else:
            # 偶数次: 工具调用后给最终答案
            if "三体" in last_user:
                content = "《三体》由刘慈欣所著, 库存充足"
            elif "R001" in last_user or "张三" in last_user:
                content = "读者 R001 张三, 信用分 100"
            else:
                content = "完成"
            return make_streamed_response(content=content)

    agent = Agent(registry=reg)
    agent.client._inner.chat.completions.create = fake_create
    from agent.robustness.retry import retry
    agent.client.chat.completions._retrying_create = retry(agent.client._policy)(fake_create)

    # 构造一组 case
    suite = CaseSuite(name="demo_mock", description="mock 演示")
    suite.cases = [
        Case(id="t1", input="查《三体》",
             asserts=[
                 {"type": "tool_called", "name": "search_books"},
                 {"type": "contains", "text": "刘慈欣"},
             ]),
        Case(id="t2", input="读者 R001 是谁",
             asserts=[
                 {"type": "tool_called", "name": "find_reader",
                  "args": {"card_no": "R001"}},
                 {"type": "contains", "text": "张三"},
             ]),
        Case(id="t3_fail", input="查《三体》",
             asserts=[
                 # 这个应该失败 - 预期含的文字不在 mock 输出里
                 {"type": "contains", "text": "黑暗森林"},
             ]),
    ]

    # 跑
    runner = EvalRunner(agent=agent)
    result = runner.run_suite(suite)

    show_run_summary(result, console)


def demo_store_and_compare():
    """演示 Store + A/B 对比"""
    console.rule("[bold]3. Store + A/B 对比[/]")

    from agent.eval.runner import RunResult, CaseResult, AssertionRunResult
    from agent.eval.store import EvalStore
    from agent.eval.viewer import show_compare
    import time

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        store = EvalStore(db_path)

        # 模拟两次 run: baseline vs experiment
        def make_run(run_id, suite, results, config):
            r = RunResult(
                run_id=run_id, suite_name=suite,
                started_at=time.time(),
                finished_at=time.time() + 1,
                config=config,
            )
            r.cases = results
            return r

        def make_case(cid, passed, cost=0.001, asserts=None):
            return CaseResult(
                case_id=cid, case_input=f"input_{cid}",
                passed=passed, cost_usd=cost,
                prompt_tokens=100, completion_tokens=20,
                duration_seconds=1.0, iterations=2,
                final_output="ok",
                assertions=asserts or [],
            )

        # Baseline: 5 个 case, 4 通过 1 失败
        baseline = make_run("baseline_001", "library_basic", [
            make_case("c1", True, cost=0.001),
            make_case("c2", True, cost=0.001),
            make_case("c3", True, cost=0.001),
            make_case("c4", False, cost=0.001),  # 失败
            make_case("c5", True, cost=0.001),
        ], config={"model": "deepseek-chat", "plan_first": False, "reflect": False})
        store.save_run(baseline)

        # Experiment: 加了 plan, c4 修好了, 但成本翻倍
        experiment = make_run("exp_001", "library_basic", [
            make_case("c1", True, cost=0.002),
            make_case("c2", True, cost=0.002),
            make_case("c3", True, cost=0.002),
            make_case("c4", True, cost=0.002),   # 修好了
            make_case("c5", False, cost=0.002),  # 退化!
        ], config={"model": "deepseek-chat", "plan_first": True, "reflect": False})
        store.save_run(experiment)

        # 对比
        diff = store.compare_runs("baseline_001", "exp_001")
        show_compare(diff, console)

    finally:
        os.unlink(db_path)


def main():
    demo_assertions()
    print()
    demo_runner_with_mock()
    print()
    demo_store_and_compare()
    print()
    console.rule("[bold]演示结束[/]")
    console.print("""
跑真实的 eval (需要 DEEPSEEK_API_KEY):

  [cyan]python eval_cli.py run evals/library/basic_queries.yaml[/]
  [cyan]python eval_cli.py run evals/library/ --plan[/]         # 对比 plan 模式
  [cyan]python eval_cli.py list[/]                              # 看历史
  [cyan]python eval_cli.py compare <run_a> <run_b>[/]           # A/B 对比
""")


if __name__ == "__main__":
    main()
