"""
example 13: Reflection 演示

让 Agent 先简短回答, 然后通过反思自评 -> 觉得不够好 -> 重答更详细。
需要 DEEPSEEK_API_KEY。
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
from agent.core import Done, ReflectionDone  # noqa: E402
from agent.strategies.reflect import make_default_reflector  # noqa: E402
from agent.tools.registry import ToolRegistry  # noqa: E402


console = Console()

registry = ToolRegistry()  # 不需要工具, 单纯回答问题

agent_for_client = Agent(registry=registry)
reflector = make_default_reflector(agent_for_client.client, "deepseek-chat")

# 一个故意挑刺的高阈值: 让反思更容易触发
agent = Agent(
    registry=registry,
    reflector=reflector,
    reflect=True,
    reflect_threshold=9,   # 极严: 9 分以下都重答
)

question = "什么是 ReAct 模式? 给一个简单的例子。"
console.print(Panel(question, title="用户问题", border_style="cyan"))
console.print()

session = Session(system_prompt="你是个回答简洁的助手, 不超过 50 字。")  # 故意让首答简短
answer_n = [0]
for evt in agent.run_stream(session, question):
    if isinstance(evt, Done):
        answer_n[0] += 1
        title = f"🤖 第 {answer_n[0]} 次回答"
        color = "yellow" if answer_n[0] == 1 else "green"
        console.print(Panel(evt.final_text, title=title, border_style=color))
        console.print()
    elif isinstance(evt, ReflectionDone):
        msg = f"评分: {evt.score}/10\n建议: {evt.suggestions}"
        if evt.retry_triggered:
            msg += "\n→ 分数 < 阈值, 触发重答"
        else:
            msg += "\n→ 分数 >= 阈值, 接受当前答案"
        console.print(Panel(msg, title="🔍 反思评估", border_style="magenta"))
        console.print()

console.print(f"[dim]总 LLM 调用: {session.usage.calls}, "
              f"tokens={session.usage.total_tokens}[/]")
console.print(f"[dim](回答 + 反思 + 可能的重答, 每次都消耗 LLM 调用)[/]")
