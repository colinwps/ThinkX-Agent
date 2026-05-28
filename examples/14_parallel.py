"""
example 14: 并行工具调用演示

模型会同时返回多个 tool_calls 时, 默认串行执行;
打开 parallel_tools=True 后并行 -> 整体时延降低。

需要 DEEPSEEK_API_KEY。
"""
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

load_dotenv()

if not os.getenv("DEEPSEEK_API_KEY"):
    print("❌ 错误: 请先在 .env 中设置 DEEPSEEK_API_KEY")
    sys.exit(1)

from agent import Agent, Session  # noqa: E402
from agent.core import Done, ToolCallStart, ToolCallResult  # noqa: E402
from agent.tools.base import tool  # noqa: E402
from agent.tools.registry import ToolRegistry  # noqa: E402


console = Console()


# 模拟"慢"工具: 每个 sleep 0.8s, 让并行效果明显
@tool
def fetch_user(user_id: str) -> str:
    """根据 user_id 获取用户基础信息"""
    time.sleep(0.8)
    return f"用户 {user_id}: 姓名=张三, 邮箱=zhang@example.com"


@tool
def fetch_orders(user_id: str) -> str:
    """获取用户的订单"""
    time.sleep(0.8)
    return f"用户 {user_id} 最近 3 单: #1001(已发货), #1002(已发货), #1003(已签收)"


@tool
def fetch_balance(user_id: str) -> str:
    """获取用户账户余额"""
    time.sleep(0.8)
    return f"用户 {user_id} 余额: ¥1234.56, 积分: 8800"


registry = ToolRegistry()
for t in [fetch_user, fetch_orders, fetch_balance]:
    registry.register(t)


prompt = """请帮我查一下用户 U001 的:
1. 基础信息
2. 最近订单
3. 账户余额

三件事彼此独立, 你可以同时查询。然后给我一个汇总报告。"""

# === 串行 ===
console.rule("[bold]Run 1: 串行 (parallel_tools=False)[/]")
agent_seq = Agent(registry=registry, parallel_tools=False)
session1 = Session()
t0 = time.time()
for evt in agent_seq.run_stream(session1, prompt):
    if isinstance(evt, ToolCallStart):
        console.print(f"[dim]→ {evt.name}({evt.arguments}) [start at {time.time()-t0:.2f}s][/]")
    elif isinstance(evt, ToolCallResult):
        console.print(f"[dim]  ← {evt.name} [done at {time.time()-t0:.2f}s][/]")
    elif isinstance(evt, Done):
        console.print(f"\n[green]最终回答:[/] {evt.final_text[:100]}...")
seq_time = time.time() - t0
console.print(f"\n[bold]串行耗时: {seq_time:.2f}s[/]\n")


# === 并行 ===
console.rule("[bold]Run 2: 并行 (parallel_tools=True)[/]")
agent_par = Agent(registry=registry, parallel_tools=True)
session2 = Session()
t0 = time.time()
for evt in agent_par.run_stream(session2, prompt):
    if isinstance(evt, ToolCallStart):
        console.print(f"[dim]→ {evt.name}({evt.arguments}) [start at {time.time()-t0:.2f}s][/]")
    elif isinstance(evt, ToolCallResult):
        console.print(f"[dim]  ← {evt.name} [done at {time.time()-t0:.2f}s][/]")
    elif isinstance(evt, Done):
        console.print(f"\n[green]最终回答:[/] {evt.final_text[:100]}...")
par_time = time.time() - t0
console.print(f"\n[bold]并行耗时: {par_time:.2f}s[/]\n")


# === 对比 ===
console.rule("[bold]对比[/]")
table = Table(border_style="dim")
table.add_column("模式"); table.add_column("耗时", justify="right"); table.add_column("说明")
table.add_row("串行", f"{seq_time:.2f}s", "工具一个接一个跑")
table.add_row("并行", f"{par_time:.2f}s", "独立工具同时跑")
if seq_time > 0:
    saving = (1 - par_time / seq_time) * 100
    table.add_row("[green]节省[/]", f"[green]{saving:.0f}%[/]", "")
console.print(table)
