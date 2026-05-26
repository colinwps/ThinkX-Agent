"""
Trace Viewer: 用 rich 把 trace 数据渲染到终端

提供 4 个核心视图:
1. list_traces:   列表(最近 N 条)
2. show_trace:    单个 trace 详情(span 树状展示)
3. show_span:     单个 span 的完整 payload
4. show_summary:  聚合统计(总成本、按模型分布)
"""
from __future__ import annotations

import datetime
import json
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from .models import Span, SpanKind, SpanStatus, Trace, TraceStatus
from .pricing import format_cost
from .store import TraceStore


# ============================================================
# 工具函数
# ============================================================

def _fmt_time(ts: float | None) -> str:
    if ts is None:
        return "-"
    return datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M:%S")


def _fmt_duration(ms: float | None) -> str:
    if ms is None:
        return "-"
    if ms < 1000:
        return f"{ms:.0f}ms"
    return f"{ms / 1000:.2f}s"


def _status_style(status: str) -> str:
    return {
        "ok": "green",
        "running": "yellow",
        "error": "red",
        "cancelled": "magenta",
    }.get(status, "white")


def _kind_icon(kind: SpanKind) -> str:
    return {
        SpanKind.AGENT_RUN: "▶",
        SpanKind.LLM_CALL: "🤖",
        SpanKind.TOOL_CALL: "🔧",
        SpanKind.APPROVAL: "🔐",
        SpanKind.ERROR: "❌",
        SpanKind.OTHER: "•",
    }.get(kind, "•")


# ============================================================
# 视图 1: trace 列表
# ============================================================

def list_traces(
    store: TraceStore,
    console: Console,
    limit: int = 20,
    session_id: Optional[str] = None,
) -> None:
    traces = store.list_traces(limit=limit, session_id=session_id)
    if not traces:
        console.print("[dim](没有 trace 记录)[/]")
        return

    table = Table(
        title=f"最近 {len(traces)} 条 trace" + (f" (session={session_id})" if session_id else ""),
        border_style="dim",
    )
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("时间", style="dim")
    table.add_column("状态", justify="center")
    table.add_column("耗时", justify="right")
    table.add_column("轮", justify="right")
    table.add_column("工具", justify="right")
    table.add_column("tokens", justify="right")
    table.add_column("成本", justify="right")
    table.add_column("用户输入", overflow="fold")

    for t in traces:
        status_str = f"[{_status_style(t.status.value)}]{t.status.value}[/]"
        preview = t.user_input[:50] + ("..." if len(t.user_input) > 50 else "")
        table.add_row(
            t.id,
            _fmt_time(t.started_at),
            status_str,
            _fmt_duration(t.duration_ms),
            str(t.iteration_count),
            str(t.tool_call_count),
            str(t.total_tokens),
            format_cost(t.total_cost_usd),
            preview,
        )
    console.print(table)


# ============================================================
# 视图 2: 单个 trace 详情(span 树)
# ============================================================

def show_trace(store: TraceStore, console: Console, trace_id: str) -> None:
    trace = store.get_trace(trace_id)
    if trace is None:
        console.print(f"[red]找不到 trace: {trace_id}[/]")
        return

    spans = store.get_spans(trace_id)

    # 顶部: trace 元信息
    header_lines = [
        f"[bold cyan]Trace ID:[/] {trace.id}",
        f"[bold]Session:[/] {trace.session_id or '(无)'}",
        f"[bold]Model:[/] {trace.model}",
        f"[bold]时间:[/] {_fmt_time(trace.started_at)} ~ {_fmt_time(trace.ended_at)} "
        f"([yellow]{_fmt_duration(trace.duration_ms)}[/])",
        f"[bold]状态:[/] [{_status_style(trace.status.value)}]{trace.status.value}[/]",
        "",
        f"[bold]统计:[/] {trace.iteration_count} 轮, "
        f"{trace.llm_call_count} 次 LLM, {trace.tool_call_count} 次工具",
        f"[bold]Tokens:[/] in={trace.total_prompt_tokens} "
        f"(cached={trace.total_cached_tokens}) out={trace.total_completion_tokens} "
        f"total={trace.total_tokens}",
        f"[bold]成本:[/] [green]{format_cost(trace.total_cost_usd)}[/]",
        "",
        f"[bold]用户输入:[/]\n{trace.user_input}",
    ]
    if trace.final_output:
        out_preview = trace.final_output[:500] + ("..." if len(trace.final_output) > 500 else "")
        header_lines.append(f"\n[bold]最终输出:[/]\n{out_preview}")
    console.print(Panel("\n".join(header_lines), border_style="cyan", title="Trace 详情"))

    # span 树
    if not spans:
        console.print("[dim](无 span)[/]")
        return

    tree = Tree(f"[bold]执行轨迹[/] ({len(spans)} spans)")
    by_parent: dict[Optional[str], list[Span]] = {}
    for s in spans:
        by_parent.setdefault(s.parent_id, []).append(s)

    def add_to_tree(parent_tree: Tree, parent_id: Optional[str]):
        for span in by_parent.get(parent_id, []):
            label = _format_span_label(span)
            sub = parent_tree.add(label)
            add_to_tree(sub, span.id)

    add_to_tree(tree, None)
    console.print(tree)

    console.print(
        f"\n[dim]用 `/span <span_id>` 看具体 span 详情(包含完整 payload)[/]"
    )


def _format_span_label(span: Span) -> str:
    """单行 span 标签 -- 显示在树里"""
    icon = _kind_icon(span.kind)
    status_color = _status_style(span.status.value)
    dur = _fmt_duration(span.duration_ms)

    parts = [f"{icon} [{status_color}]{span.kind.value}[/]", f"[bold]{span.name}[/]"]

    # 不同类型加不同的关键信息
    if span.kind == SpanKind.LLM_CALL:
        attrs = span.attributes
        tokens = f"in={attrs.get('prompt_tokens', 0)} out={attrs.get('completion_tokens', 0)}"
        cost = format_cost(attrs.get("cost_usd", 0))
        finish = attrs.get("finish_reason", "")
        parts.append(f"[dim]{tokens} {cost} ({finish})[/]")
    elif span.kind == SpanKind.TOOL_CALL:
        approved = span.attributes.get("approved", True)
        flag = "[green]✓[/]" if approved else "[red]✗[/]"
        parts.append(flag)
    elif span.kind == SpanKind.APPROVAL:
        d = span.attributes.get("decision", "")
        parts.append(f"[dim]→ {d}[/]")

    parts.append(f"[dim]({dur})[/]")

    if span.error:
        parts.append(f"[red]ERR: {span.error[:60]}[/]")

    parts.append(f"[dim]id={span.id[:8]}[/]")
    return "  ".join(parts)


# ============================================================
# 视图 3: span 详情(完整 payload)
# ============================================================

def show_span(store: TraceStore, console: Console, span_id: str) -> None:
    """全 store 扫一遍找 span -- 因为没建 id 索引上的 single-get 接口"""
    # 简单实现: 让用户传 span_id 前缀, 我们自己 scan
    # 实际中 span 数量不大, 这样可以接受
    # 更优雅的做法是在 store 里加 get_span(span_id)
    found: Optional[Span] = None

    # 借用一下 sqlite 直接查(避开 trace_id 依赖)
    import sqlite3
    from contextlib import closing
    conn = sqlite3.connect(store.db_path)
    try:
        cur = conn.execute(
            "SELECT * FROM spans WHERE id LIKE ? LIMIT 2",
            (span_id + "%",),
        )
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        console.print(f"[red]找不到 span: {span_id}[/]")
        return
    if len(rows) > 1:
        console.print(f"[yellow]前缀 {span_id} 匹配到多个, 请提供更长的 id[/]")
        return

    found = store._row_to_span(rows[0], cols)

    # 渲染
    header = [
        f"[bold]ID:[/] {found.id}  [dim](trace={found.trace_id}, parent={found.parent_id or '-'})[/]",
        f"[bold]Kind:[/] {found.kind.value}  [bold]Name:[/] {found.name}",
        f"[bold]Status:[/] [{_status_style(found.status.value)}]{found.status.value}[/]  "
        f"[bold]耗时:[/] {_fmt_duration(found.duration_ms)}",
    ]
    if found.error:
        header.append(f"[red]Error:[/] {found.error}")
    console.print(Panel("\n".join(header), border_style="cyan", title="Span"))

    # attributes
    if found.attributes:
        table = Table(title="Attributes", border_style="dim", show_header=False)
        table.add_column(style="cyan")
        table.add_column()
        for k, v in found.attributes.items():
            val_str = str(v)
            if len(val_str) > 200:
                val_str = val_str[:200] + "..."
            table.add_row(k, val_str)
        console.print(table)

    # payload: 用 syntax highlight 显示 JSON
    if found.payload:
        for key, value in found.payload.items():
            if isinstance(value, (dict, list)):
                pretty = json.dumps(value, ensure_ascii=False, indent=2)
            else:
                pretty = str(value)
            console.print(f"\n[bold cyan]payload.{key}:[/]")
            # 短文本不高亮
            if len(pretty) < 200 and not isinstance(value, (dict, list)):
                console.print(Panel(pretty, border_style="dim"))
            else:
                try:
                    console.print(Syntax(pretty, "json", theme="monokai", line_numbers=False))
                except Exception:
                    console.print(pretty)


# ============================================================
# 视图 4: 聚合统计
# ============================================================

def show_summary(
    store: TraceStore,
    console: Console,
    since: Optional[float] = None,
    until: Optional[float] = None,
) -> None:
    agg = store.aggregate_cost(since=since, until=until)
    by_model = store.aggregate_by_model()

    lines = [
        f"[bold]Trace 数:[/] {agg['trace_count']}",
        f"[bold]LLM 调用:[/] {agg['llm_calls']}",
        f"[bold]工具调用:[/] {agg['tool_calls']}",
        f"[bold]总输入 tokens:[/] {agg['prompt_tokens']} (cached: {agg['cached_tokens']})",
        f"[bold]总输出 tokens:[/] {agg['completion_tokens']}",
        f"[bold]总成本:[/] [green]{format_cost(agg['cost_usd'])}[/]",
    ]
    console.print(Panel("\n".join(lines), border_style="green", title="累计统计"))

    if by_model:
        table = Table(title="按模型分布", border_style="dim")
        table.add_column("模型", style="cyan")
        table.add_column("Trace 数", justify="right")
        table.add_column("LLM 调用", justify="right")
        table.add_column("输入 tokens", justify="right")
        table.add_column("输出 tokens", justify="right")
        table.add_column("成本", justify="right")
        for row in by_model:
            table.add_row(
                row["model"],
                str(row["traces"]),
                str(row["calls"]),
                str(row["prompt_tokens"]),
                str(row["completion_tokens"]),
                format_cost(row["cost_usd"] or 0),
            )
        console.print(table)
