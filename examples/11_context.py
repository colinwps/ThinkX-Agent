"""
Step 6: 上下文管理 (Prompt Cache + 历史压缩 + Token 预算)

演示:
1. 不调真 LLM 的纯本地功能(估算 + 压缩器单元)
2. 真跑长对话观察压缩何时触发(需要 DEEPSEEK_API_KEY)

跑法:
    python -m examples.11_context              # 纯本地演示
    python -m examples.11_context --real-llm   # 真调 LLM 跑长对话
"""
import os
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

console = Console()


# ============================================================
# Part 1: Token 估算 (零依赖, 不调 API)
# ============================================================

def demo_token_estimator():
    console.rule("[bold]1. Token 估算 (本地)[/]")
    from agent.context import estimate_messages_tokens, estimate_tokens

    cases = [
        "hello",
        "你好",
        "Hello, world! 你好, 世界!",
        "x" * 1000,
        "测试" * 500,
    ]
    table = Table(border_style="dim")
    table.add_column("文本"); table.add_column("字符数", justify="right"); table.add_column("估算 token", justify="right")
    for t in cases:
        preview = t if len(t) <= 30 else t[:27] + "..."
        table.add_row(repr(preview), str(len(t)), str(estimate_tokens(t)))
    console.print(table)

    # messages 估算
    messages = [
        {"role": "system", "content": "你是一个助手"},
        {"role": "user", "content": "查《三体》"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "search_books", "arguments": '{"query":"三体"}'}}
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": "找到 3 本: 《三体》..."},
        {"role": "assistant", "content": "三体系列共 3 本"},
    ]
    total = estimate_messages_tokens(messages)
    console.print(f"\n5 条 messages (含 tool_call) 估算: [yellow]{total}[/] tokens")


# ============================================================
# Part 2: 摘要压缩 (用 mock 不调 API)
# ============================================================

def demo_compression_mock():
    console.rule("[bold]2. 历史压缩 (mock LLM)[/]")
    from agent.context import HistoryCompressor

    # mock 一个 LLM 调用
    def fake_summarize(prompt: str) -> str:
        return ("用户先查询了三体图书信息(3 本, 库存充足), "
                "然后查了读者 R001 张三的借阅记录(1 本未还), "
                "最后请求把统计写入文件被拒绝。")

    compressor = HistoryCompressor(llm_call=fake_summarize)

    # 构造一段长对话
    messages = []
    for i in range(15):
        messages.append({"role": "user", "content": f"问题 {i}: 详细询问关于业务的事情" * 5})
        messages.append({"role": "assistant", "content": f"回答 {i}: 详尽的回答" * 8})

    new_msgs, result = compressor.compress(messages, keep_recent=6)

    table = Table(border_style="dim")
    table.add_column("指标", style="cyan"); table.add_column("值", justify="right")
    table.add_row("压缩前消息数", str(result.messages_before))
    table.add_row("压缩后消息数", str(result.messages_after))
    table.add_row("压缩前 tokens", str(result.tokens_before))
    table.add_row("压缩后 tokens", str(result.tokens_after))
    table.add_row("节省 tokens", str(result.saved_tokens))
    table.add_row("压缩率", f"{result.compression_ratio*100:.1f}%")
    console.print(table)

    console.print(f"\n[dim]生成的摘要:[/]\n{result.summary}\n")
    console.print(f"[dim]新消息布局: {[m['role'] for m in new_msgs]}[/]")


# ============================================================
# Part 3: Budget 触发逻辑(mock)
# ============================================================

def demo_budget_mock():
    console.rule("[bold]3. 预算检查 + 自动压缩 (mock)[/]")
    from agent.context import BudgetManager, HistoryCompressor

    def fake_summarize(prompt: str) -> str:
        return "(早期对话已压缩为摘要)"

    compressor = HistoryCompressor(llm_call=fake_summarize)

    # 设个很小的预算, 强行触发
    budget = BudgetManager(
        compressor=compressor,
        max_tokens=2_000,
        threshold_ratio=0.5,   # 1000 tokens 就触发
        keep_recent=4,
    )

    # 短对话: 不触发
    short = [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    _, check = budget.check_and_compress(short)
    console.print(f"短对话 ({check.current_tokens} tokens) → "
                  f"超阈值: [{'red' if check.over_threshold else 'green'}]{check.over_threshold}[/]")

    # 长对话: 触发
    long = [{"role": "system", "content": "你是助手"}]
    for i in range(20):
        long.append({"role": "user", "content": f"问题 {i}, " * 30})
        long.append({"role": "assistant", "content": f"回答 {i}, " * 30})

    new_msgs, check = budget.check_and_compress(long)
    console.print(f"\n长对话 ({check.current_tokens} tokens) → "
                  f"超阈值: [red]{check.over_threshold}[/], "
                  f"已压缩: [green]{check.compressed}[/]")
    if check.compression:
        c = check.compression
        console.print(f"  [dim]{c.messages_before} → {c.messages_after} 条 "
                      f"({c.tokens_before} → {c.tokens_after} tokens, "
                      f"节省 {c.compression_ratio*100:.0f}%)[/]")


# ============================================================
# Part 4: 真跑 LLM 看长对话压缩 (需要 API key)
# ============================================================

def demo_real_long_conversation():
    if not os.getenv("DEEPSEEK_API_KEY"):
        console.print("[yellow]跳过真实 LLM 测试 (DEEPSEEK_API_KEY 未设置)[/]")
        return

    console.rule("[bold]4. 真实长对话 + 压缩 + cache[/]")

    from agent import Agent, Session
    from agent.context import default_budget
    from agent.context.compressor import make_default_compressor
    from agent.core import ContextCompressed
    from agent.tools.builtin import BUILTIN_TOOLS
    from agent.tools.registry import ToolRegistry

    registry = ToolRegistry()
    registry.register_many(BUILTIN_TOOLS)

    # 创建 budget + compressor: 用很小的阈值便于演示
    agent_for_compress = Agent(registry=registry)
    compressor = make_default_compressor(agent_for_compress.client, "deepseek-chat")
    budget = default_budget("deepseek-chat", compressor=compressor)
    # 强行设小阈值
    budget.max_tokens = 4_000
    budget.threshold_ratio = 0.6  # 2400 tokens 触发

    agent = Agent(
        registry=registry,
        budget_manager=budget,
    )
    session = Session(system_prompt="你是有用的助手, 回答务必详细 (200 字以上)")

    queries = [
        "用 200 字介绍古希腊神话的奥林匹斯十二主神",
        "再用 200 字讲讲北欧神话的奥丁",
        "中国神话里的盘古和西方创世神话比有什么不同?",
        "总结一下我们讨论了哪些神话体系",
    ]

    for i, q in enumerate(queries, 1):
        console.print(f"\n[bold cyan]── 第 {i} 轮 ──[/]")
        console.print(f"[cyan]用户:[/] {q}")
        for evt in agent.run_stream(session, q):
            from agent.core import (Done, TextChunk, ToolCallStart,
                                    ToolCallResult, IterationStart)
            if isinstance(evt, ContextCompressed):
                console.print(
                    f"  [yellow]✨ 触发压缩: "
                    f"{evt.messages_before} → {evt.messages_after} 条, "
                    f"{evt.tokens_before} → {evt.tokens_after} tokens[/]"
                )
            elif isinstance(evt, Done):
                console.print(f"[green]助手:[/] {evt.final_text[:120]}...")
        console.print(f"  [dim]当前 session: {len(session.messages)} 条消息, "
                      f"约 {budget.estimate(session.to_llm_messages())} tokens[/]")


# ============================================================
# main
# ============================================================

def main():
    project_root = Path(__file__).parent.parent
    sys.path.insert(0, str(project_root))

    # load_dotenv 在 import 之前
    from dotenv import load_dotenv
    load_dotenv()

    use_real_llm = "--real-llm" in sys.argv

    demo_token_estimator()
    print()
    demo_compression_mock()
    print()
    demo_budget_mock()
    print()
    if use_real_llm:
        demo_real_long_conversation()
    else:
        console.rule("[dim]4. 真实 LLM 长对话演示[/]")
        console.print("[dim](跳过 - 加 --real-llm 参数运行)[/]")


if __name__ == "__main__":
    main()
