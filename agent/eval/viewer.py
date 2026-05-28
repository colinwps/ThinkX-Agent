"""
Eval Viewer: 终端报告

提供:
- show_run_summary: run 总结 (Rich Panel)
- show_case_detail: 单个 case 的完整断言列表
- show_run_table: 多个 run 横向对比
- show_compare: A/B diff
"""
from __future__ import annotations

import datetime

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .runner import CaseResult, RunResult
from ..observability.pricing import format_cost


def _fmt_time(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M:%S")


def show_run_summary(run: RunResult, console: Console) -> None:
    """跑完一组 case 后的 dashboard"""
    pass_rate = run.pass_rate * 100
    color = "green" if pass_rate >= 90 else "yellow" if pass_rate >= 60 else "red"

    header = [
        f"[bold]Suite:[/] {run.suite_name}",
        f"[bold]Run ID:[/] {run.run_id}",
        f"[bold]时间:[/] {_fmt_time(run.started_at)} (耗时 {run.duration:.1f}s)",
        "",
        f"[bold]通过率:[/] [{color}]{run.pass_count} / {run.total_count} ({pass_rate:.0f}%)[/]",
        f"[bold]总 tokens:[/] {run.total_tokens:,}",
        f"[bold]总成本:[/] [green]{format_cost(run.total_cost_usd)}[/]",
        "",
        "[bold]配置:[/]",
    ]
    for k, v in run.config.items():
        header.append(f"  {k}: {v}")

    console.print(Panel("\n".join(header), border_style=color, title="Eval Run 结果"))

    # case 列表
    table = Table(title="Case 详情", border_style="dim", show_lines=False)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("状态", justify="center")
    table.add_column("断言", justify="center")
    table.add_column("工具", justify="right")
    table.add_column("耗时", justify="right")
    table.add_column("tokens", justify="right")
    table.add_column("成本", justify="right")
    table.add_column("用户输入", overflow="fold")

    for cr in run.cases:
        status = "[green]✓ PASS[/]" if cr.passed else "[red]✗ FAIL[/]"
        if cr.error:
            status = f"[red]ERR[/]"
        assertion_text = f"{cr.pass_count}/{cr.total_assertions}"
        if cr.pass_count < cr.total_assertions:
            assertion_text = f"[red]{assertion_text}[/]"

        table.add_row(
            cr.case_id,
            status,
            assertion_text,
            str(len(cr.tool_calls)),
            f"{cr.duration_seconds:.1f}s",
            f"{cr.prompt_tokens + cr.completion_tokens}",
            format_cost(cr.cost_usd),
            cr.case_input[:50] + ("..." if len(cr.case_input) > 50 else ""),
        )

    console.print(table)


def show_case_detail(cr: CaseResult, console: Console) -> None:
    """单个 case 的完整详情"""
    status = "[green]PASS[/]" if cr.passed else "[red]FAIL[/]"
    lines = [
        f"[bold]Case ID:[/] {cr.case_id}  [bold]状态:[/] {status}",
        f"[bold]用户输入:[/] {cr.case_input}",
        "",
        f"[bold]工具调用 ({len(cr.tool_calls)}):[/]",
    ]
    for tc in cr.tool_calls:
        args_str = str(tc.get("arguments", {}))[:80]
        result_str = (tc.get("result") or "")[:80].replace("\n", " ")
        approved = "✓" if tc.get("approved", True) else "✗"
        lines.append(f"  {approved} {tc['name']}({args_str})")
        lines.append(f"      → {result_str}")
    if not cr.tool_calls:
        lines.append("  (无)")

    lines.append("")
    lines.append("[bold]最终输出:[/]")
    output_preview = cr.final_output[:400] + ("..." if len(cr.final_output) > 400 else "")
    lines.append(output_preview)

    if cr.error:
        lines.append("")
        lines.append(f"[bold red]错误:[/] {cr.error}")

    lines.append("")
    lines.append("[bold]断言:[/]")
    for a in cr.assertions:
        icon = "[green]✓[/]" if a.passed else "[red]✗[/]"
        score_str = f" [{a.score:.1f}/10]" if a.score > 0 else ""
        lines.append(f"  {icon} [{a.type}]{score_str} {a.label}")
        if not a.passed or a.message != "ok":
            lines.append(f"      [dim]{a.message}[/]")

    console.print(Panel("\n".join(lines), border_style="cyan" if cr.passed else "red",
                        title=f"Case: {cr.case_id}"))


def show_run_table(runs: list[dict], console: Console) -> None:
    """列表展示历史 runs"""
    if not runs:
        console.print("[dim](无历史 run)[/]")
        return
    table = Table(title=f"最近 {len(runs)} 次 eval run", border_style="dim")
    table.add_column("Run ID", style="cyan")
    table.add_column("Suite", style="cyan")
    table.add_column("时间", style="dim")
    table.add_column("通过", justify="right")
    table.add_column("成本", justify="right")
    table.add_column("Model")
    table.add_column("配置")

    for r in runs:
        rate = r["pass_count"] / max(1, r["total_count"]) * 100
        rate_color = "green" if rate >= 90 else "yellow" if rate >= 60 else "red"
        import json
        try:
            cfg = json.loads(r["config_json"])
        except Exception:
            cfg = {}
        flags = []
        if cfg.get("plan_first"):
            flags.append("plan")
        if cfg.get("reflect"):
            flags.append("reflect")
        if cfg.get("parallel_tools"):
            flags.append("parallel")
        flags_str = ",".join(flags) or "-"

        table.add_row(
            r["run_id"],
            r["suite_name"],
            _fmt_time(r["started_at"]),
            f"[{rate_color}]{r['pass_count']}/{r['total_count']} ({rate:.0f}%)[/]",
            format_cost(r["total_cost_usd"]),
            cfg.get("model", "-"),
            flags_str,
        )
    console.print(table)


def show_compare(diff: dict, console: Console) -> None:
    """A/B 对比报告"""
    a = diff["a"]
    b = diff["b"]
    diffs = diff["diffs"]

    rate_a = a["pass_count"] / max(1, a["total_count"]) * 100
    rate_b = b["pass_count"] / max(1, b["total_count"]) * 100

    lines = [
        f"[bold]A:[/] {a['run_id']} (suite={a['suite_name']}) "
        f"通过率 {a['pass_count']}/{a['total_count']} ({rate_a:.0f}%) "
        f"成本 {format_cost(a['total_cost_usd'])}",
        f"[bold]B:[/] {b['run_id']} (suite={b['suite_name']}) "
        f"通过率 {b['pass_count']}/{b['total_count']} ({rate_b:.0f}%) "
        f"成本 {format_cost(b['total_cost_usd'])}",
    ]
    delta_rate = rate_b - rate_a
    delta_cost = b["total_cost_usd"] - a["total_cost_usd"]
    rate_str = f"[green]+{delta_rate:.1f}%[/]" if delta_rate >= 0 else f"[red]{delta_rate:.1f}%[/]"
    cost_str = f"[red]+{delta_cost:.4f}[/]" if delta_cost > 0 else f"[green]{delta_cost:.4f}[/]"
    lines.append(f"")
    lines.append(f"[bold]通过率变化:[/] {rate_str}")
    lines.append(f"[bold]成本变化:[/] ${cost_str}")
    console.print(Panel("\n".join(lines), border_style="cyan", title="A/B 对比"))

    if not diffs:
        console.print("[green]✓ 完全一致, 无 case 级差异[/]")
        return

    table = Table(title="Case 级差异", border_style="dim")
    table.add_column("Case ID", style="cyan")
    table.add_column("状态")
    table.add_column("说明")

    for d in diffs:
        cid = d["case_id"]
        st = d["status"]
        if st == "regressed":
            color = "red"
            note = f"A: PASS, B: FAIL ⚠"
        elif st == "improved":
            color = "green"
            note = f"A: FAIL, B: PASS ✨"
        elif st == "only_in_a":
            color = "yellow"
            note = "只在 A 中"
        elif st == "only_in_b":
            color = "yellow"
            note = "只在 B 中"
        elif st == "same":
            cd = d.get("cost_delta", 0)
            color = "dim"
            note = f"通过状态相同, 成本变化 ${cd:+.4f}"
        else:
            color = "dim"
            note = st
        table.add_row(cid, f"[{color}]{st}[/]", note)
    console.print(table)
