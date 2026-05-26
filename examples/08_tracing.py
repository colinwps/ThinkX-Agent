"""
Step B: 可观测性 —— 跑完任务后看 trace 数据

演示:
1. 创建 Tracer + TraceStore
2. 给 Agent 装上 tracer
3. 跑一个任务, trace 自动记录
4. 用 viewer 查看 trace
5. 演示脱敏功能
"""
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console

load_dotenv()

from agent import Agent, Session  # noqa: E402
from agent.observability import (  # noqa: E402
    RegexRedactor,
    TraceStore,
    Tracer,
    list_traces,
    show_summary,
    show_trace,
)
from agent.tools.builtin import BUILTIN_TOOLS  # noqa: E402
from agent.tools.registry import ToolRegistry  # noqa: E402


if not os.getenv("DEEPSEEK_API_KEY"):
    print("错误: 请在 .env 中设置 DEEPSEEK_API_KEY")
    sys.exit(1)


console = Console()

# ============================================================
# 1. 创建 Tracer
# ============================================================
db_path = "/tmp/tracing_demo.db"
# 重新跑示例时清掉旧库
Path(db_path).unlink(missing_ok=True)

trace_store = TraceStore(db_path)

# 启用脱敏 —— 假装数据里可能混入手机号 / API Key
tracer = Tracer(
    store=trace_store,
    redactor=RegexRedactor.with_common_patterns(),
)

# ============================================================
# 2. 组装 Agent 并接入 tracer
# ============================================================
registry = ToolRegistry()
registry.register_many(BUILTIN_TOOLS)
agent = Agent(registry=registry, tracer=tracer)


# ============================================================
# 3. 跑几个任务
# ============================================================
console.print("[bold]=== 跑任务 1: 简单计算 ===[/]")
s1 = Session()
agent.run(s1, "计算 (123 + 456) * 7")

console.print("\n[bold]=== 跑任务 2: 多工具组合 ===[/]")
s2 = Session()
agent.run(
    s2,
    "在 /tmp/trace_demo.txt 写入文字 '客户手机号是 13812345678, key 是 sk-abc123def456ghi789jkl', "
    "然后读回来",
)

console.print("\n[bold]=== 跑任务 3: 让模型多想几步 ===[/]")
s3 = Session()
agent.run(
    s3,
    "用 3 段话, 每段 30 字, 介绍 ReAct 模式。然后计算 12345 的平方。",
)


# ============================================================
# 4. 查看 trace
# ============================================================
console.print("\n[bold]=== Trace 列表 ===[/]")
list_traces(trace_store, console, limit=10)

console.print("\n[bold]=== 最近一次 trace 的详细执行轨迹 ===[/]")
recent = trace_store.list_traces(limit=1)
if recent:
    show_trace(trace_store, console, recent[0].id)

console.print("\n[bold]=== 总成本/统计 ===[/]")
show_summary(trace_store, console)


# ============================================================
# 5. 验证脱敏生效
# ============================================================
console.print("\n[bold]=== 验证脱敏: 任务 2 应该看不到原始手机号/key ===[/]")
trace2 = trace_store.list_traces(limit=10)
# 找到任务 2 的 trace
task2 = next((t for t in trace2 if "13812345678" in t.user_input or "客户手机号" in t.user_input), None)
if task2:
    spans = trace_store.get_spans(task2.id)
    found_phone = False
    found_key = False
    found_redacted_phone = False
    found_redacted_key = False
    for s in spans:
        payload_str = str(s.payload)
        if "13812345678" in payload_str:
            found_phone = True
        if "sk-abc123def456ghi789jkl" in payload_str:
            found_key = True
        if "REDACTED_PHONE" in payload_str:
            found_redacted_phone = True
        if "REDACTED_API_KEY" in payload_str:
            found_redacted_key = True

    # user_input 字段是不脱敏的(因为本身就是用户输入), 但 payload 应该脱敏
    console.print(f"  user_input 里手机号原文: {found_phone}  (符合预期: True, 这是原始输入)")
    console.print(f"  span.payload 里 [REDACTED_PHONE]: {found_redacted_phone}  (符合预期: True)")
    console.print(f"  span.payload 里 [REDACTED_API_KEY]: {found_redacted_key}  (符合预期: True)")

console.print(
    f"\n数据库已存到 {db_path}, 用 "
    f"`python trace_cli.py --db {db_path} list` 查看"
)
