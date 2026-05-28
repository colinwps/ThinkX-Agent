"""
example 12: Plan-and-Execute 演示

需要 DEEPSEEK_API_KEY (调真 LLM 才能看到 plan 效果)。
跑法: python -m examples.12_planning
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel

load_dotenv()

if not os.getenv("DEEPSEEK_API_KEY"):
    print("❌ 错误: 请先在 .env 中设置 DEEPSEEK_API_KEY")
    sys.exit(1)

from agent import Agent, Session  # noqa: E402
from agent.core import Done, PlanGenerated, ToolCallStart, ToolCallResult  # noqa: E402
from agent.strategies.plan import make_default_planner  # noqa: E402
from agent.tools.builtin import BUILTIN_TOOLS  # noqa: E402
from agent.tools.registry import ToolRegistry  # noqa: E402


console = Console()

registry = ToolRegistry()
registry.register_many(BUILTIN_TOOLS)

# 用同一个 client 做 planner
agent_for_client = Agent(registry=registry)
planner = make_default_planner(agent_for_client.client, "deepseek-chat")

agent = Agent(
    registry=registry,
    planner=planner,
    plan_first=True,
)

# 一个适合用 Plan 的复杂任务
task = """请帮我做这件事:
1. 在 /tmp/notes/ 目录下创建一个 daily.md 文件
2. 写入今天的待办清单(随便编 3 个)
3. 然后读回来验证内容
4. 最后告诉我文件大小"""

console.print(Panel(task, title="用户任务", border_style="cyan"))
console.print()

session = Session()
for evt in agent.run_stream(session, task):
    if isinstance(evt, PlanGenerated):
        console.print(Panel(
            "\n".join(f"{i+1}. {s}" for i, s in enumerate(evt.steps)),
            title="🗺️ 执行计划",
            border_style="yellow",
        ))
        console.print()
    elif isinstance(evt, ToolCallStart):
        console.print(f"[dim]→ {evt.name}({evt.arguments})[/]")
    elif isinstance(evt, ToolCallResult):
        preview = evt.result[:80].replace("\n", " ")
        console.print(f"[dim]  ← {preview}...[/]")
    elif isinstance(evt, Done):
        console.print()
        console.print(Panel(evt.final_text, title="🤖 最终回答", border_style="green"))

console.print()
console.print(f"[dim]总 LLM 调用: {session.usage.calls}, "
              f"tokens={session.usage.total_tokens}[/]")
