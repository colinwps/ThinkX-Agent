"""
Step A2: 流式输出

体验:
- 文字逐字到达, 不是等完整响应才显示
- 通过事件流(run_stream)处理, 调用方可以自定义渲染
"""
import os
import sys

from dotenv import load_dotenv

load_dotenv()

from agent import Agent, Session  # noqa: E402
from agent.core import (  # noqa: E402
    Aborted,
    Done,
    IterationStart,
    TextChunk,
    ToolCallResult,
    ToolCallStart,
)
from agent.tools.builtin import BUILTIN_TOOLS  # noqa: E402
from agent.tools.registry import ToolRegistry  # noqa: E402


if not os.getenv("DEEPSEEK_API_KEY"):
    print("错误: 请在 .env 中设置 DEEPSEEK_API_KEY")
    sys.exit(1)


registry = ToolRegistry()
registry.register_many(BUILTIN_TOOLS)

agent = Agent(registry=registry)
session = Session()


def render(events):
    """自定义事件渲染 —— 你可以按需要怎么显示"""
    for e in events:
        if isinstance(e, IterationStart):
            if e.n > 1:
                print(f"\n--- 第 {e.n} 轮 ---")
        elif isinstance(e, TextChunk):
            # 逐字打印, flush 让它立刻显示
            print(e.text, end="", flush=True)
        elif isinstance(e, ToolCallStart):
            print(f"\n[调用工具] {e.name}({e.arguments})")
        elif isinstance(e, ToolCallResult):
            preview = e.result[:120] + ("..." if len(e.result) > 120 else "")
            print(f"[结果] {preview}")
        elif isinstance(e, Done):
            print()  # 最终回答后换行
        elif isinstance(e, Aborted):
            print(f"\n[终止] {e.reason}")


# 长一点的回答, 流式效果才明显
print("用户: 请用 3 段话, 每段 50 字左右, 介绍一下 ReAct Agent 的工作原理。\n")
render(agent.run_stream(session, "请用 3 段话, 每段 50 字左右, 介绍一下 ReAct Agent 的工作原理。"))

print("\n\n用户: 现在算一下 (12345 * 67) - 8901 是多少, 算完用一句话总结。\n")
render(agent.run_stream(session, "现在算一下 (12345 * 67) - 8901 是多少, 算完用一句话总结。"))

print(f"\n本次会话 token 用量: {session.usage.summary()}")
