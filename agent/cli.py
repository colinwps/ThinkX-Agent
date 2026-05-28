"""
CLI REPL —— 主题 A 的最终产品形态

特性:
- 多行输入(支持 \\ 续行, 或 Ctrl-V Enter 换行)
- 命令历史持久化(↑↓ 翻历史, Ctrl-R 反向搜索)
- 流式输出(模型边想边显示)
- 工具调用确认(危险工具会弹出 y/n)
- 内置斜杠命令: /help /clear /tokens /save /load /sessions /yolo /careful /tools /quit
- 会话自动保存到 SQLite
"""
from __future__ import annotations

import datetime
import json
import sys
from typing import Callable

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .approval import ApprovalPolicy, Decision
from .core import (
    Agent,
    Aborted,
    Done,
    IterationStart,
    TextChunk,
    ToolCallResult,
    ToolCallStart,
)
from .observability import list_traces, show_span, show_summary, show_trace
from .session import Session, SessionStore


console = Console()


# ============================================================
# 工具确认: 同步阻塞地问用户 y/n
# ============================================================

def make_cli_approval(console: Console) -> Callable[[str, dict], tuple[bool, str]]:
    """生成一个 CLI 的审批回调"""

    def ask(tool_name: str, arguments: dict) -> tuple[bool, str]:
        # 用 rich 把工具调用展示得醒目一点
        args_str = json.dumps(arguments, ensure_ascii=False, indent=2)
        console.print()
        console.print(Panel(
            f"[bold yellow]即将执行工具:[/] [cyan]{tool_name}[/]\n"
            f"[dim]参数:[/]\n{args_str}",
            border_style="yellow",
            title="⚠ 需要确认",
        ))

        while True:
            try:
                # 用 input 而非 prompt_toolkit, 避免和主 REPL 的 session 冲突
                answer = input("是否执行? [y]es / [n]o / [a]lways(本次会话不再问) > ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                console.print("[red]已取消[/]")
                return False, "用户中断"

            if answer in ("y", "yes", ""):
                return True, ""
            if answer in ("n", "no"):
                reason = input("(可选)告诉模型为什么拒绝, 回车跳过 > ").strip()
                return False, reason
            if answer in ("a", "always"):
                # 把这个工具加入 always_allow —— 通过闭包向外传递信号
                ask.last_decision = "always"
                ask.last_tool = tool_name
                return True, ""
            console.print("[dim]请输入 y / n / a[/]")

    ask.last_decision = None  # 用于和外层 REPL 通信
    ask.last_tool = None
    return ask


# ============================================================
# REPL 主类
# ============================================================

class REPL:
    def __init__(
        self,
        agent: Agent,
        store: SessionStore,
        trace_store=None,  # Optional[TraceStore]
        history_file: str = "~/.my-agent/repl_history",
    ):
        self.agent = agent
        self.store = store
        self.trace_store = trace_store  # 可以从 agent.tracer.store 拿, 但这里显式存
        self.session: Session | None = None  # 当前活跃会话

        # 把 cli 审批回调装到 agent 上
        self._approval_fn = make_cli_approval(console)
        self.agent.approval_callback = self._approval_fn

        # prompt_toolkit 设置: 历史 + 多行 + 风格
        import os
        os.makedirs(os.path.expanduser("~/.my-agent"), exist_ok=True)
        self.prompt = PromptSession(
            history=FileHistory(os.path.expanduser(history_file)),
            multiline=False,  # 单行模式; 多行用 \ 续行(下面有键绑定)
            key_bindings=self._build_keybindings(),
        )

    # ---------- 命令绑定 ----------

    def _build_keybindings(self) -> KeyBindings:
        kb = KeyBindings()
        # 这里可以加自定义快捷键, 比如 Ctrl-N 新建会话, 暂时留空
        return kb

    # ---------- 命令分发 ----------

    COMMANDS = {
        "/help": "显示帮助",
        "/clear": "清空当前会话历史(保留 system prompt)",
        "/new": "新建一个会话",
        "/tokens": "显示当前会话的 token 用量",
        "/sessions": "列出所有保存的会话",
        "/load <id>": "加载一个会话",
        "/save": "保存当前会话(其实是自动保存的)",
        "/title <新标题>": "重命名当前会话",
        "/tools": "列出所有可用工具",
        "/skills": "列出所有可用 skill",
        "/yolo": "切换到全自动模式(不再询问)",
        "/careful": "切换到所有工具都要确认的偏执模式",
        "/safe": "回到默认安全模式(危险工具要确认)",
        "/traces [n]": "列出最近 n 条 trace (默认 10)",
        "/trace <id>": "查看某个 trace 的详细执行轨迹",
        "/span <id>": "查看某个 span 的完整 payload",
        "/cost": "显示总成本/token 统计",
        "/quit, /exit, /q": "退出",
    }

    def handle_command(self, line: str) -> bool:
        """返回 True 表示是命令(已处理), False 表示是普通输入"""
        if not line.startswith("/"):
            return False

        parts = line.strip().split(maxsplit=1)
        cmd = parts[0]
        arg = parts[1] if len(parts) > 1 else ""

        if cmd in ("/quit", "/exit", "/q"):
            raise EOFError
        elif cmd == "/help":
            self.cmd_help()
        elif cmd == "/clear":
            self.cmd_clear()
        elif cmd == "/new":
            self.cmd_new()
        elif cmd == "/tokens":
            self.cmd_tokens()
        elif cmd == "/sessions":
            self.cmd_sessions()
        elif cmd == "/load":
            self.cmd_load(arg)
        elif cmd == "/save":
            self.cmd_save()
        elif cmd == "/title":
            self.cmd_title(arg)
        elif cmd == "/tools":
            self.cmd_tools()
        elif cmd == "/skills":
            self.cmd_skills()
        elif cmd == "/yolo":
            self.agent.approval_policy = ApprovalPolicy.yolo()
            console.print("[yellow]⚡ YOLO 模式: 所有工具自动执行[/]")
        elif cmd == "/careful":
            self.agent.approval_policy = ApprovalPolicy.paranoid()
            console.print("[cyan]🐢 偏执模式: 所有工具都会询问[/]")
        elif cmd == "/safe":
            self.agent.approval_policy = ApprovalPolicy.default_safe()
            console.print("[green]🛡  安全模式: 危险工具会询问[/]")
        elif cmd == "/traces":
            self.cmd_traces(arg)
        elif cmd == "/trace":
            self.cmd_trace(arg)
        elif cmd == "/span":
            self.cmd_span(arg)
        elif cmd == "/cost":
            self.cmd_cost()
        else:
            console.print(f"[red]未知命令: {cmd}[/], 输入 /help 查看")
        return True

    # ---------- 各命令实现 ----------

    def cmd_help(self):
        table = Table(title="可用命令", show_header=False, border_style="dim")
        table.add_column("命令", style="cyan")
        table.add_column("说明")
        for k, v in self.COMMANDS.items():
            table.add_row(k, v)
        console.print(table)

    def cmd_clear(self):
        if self.session:
            self.session.clear(keep_system=True)
            self.store.save(self.session)
            console.print("[dim]会话历史已清空[/]")

    def cmd_new(self):
        self._new_session()
        console.print(f"[green]已创建新会话: {self.session.id}[/]")

    def cmd_tokens(self):
        if self.session:
            console.print(f"[dim]Token 用量: {self.session.usage.summary()}[/]")
            console.print(f"[dim]消息条数: {len(self.session.messages)}[/]")
            console.print(f"[dim]对话轮次: {self.session.turn_count()}[/]")

    def cmd_sessions(self):
        rows = self.store.list_sessions(limit=20)
        if not rows:
            console.print("[dim](还没有保存的会话)[/]")
            return
        table = Table(title="最近会话", border_style="dim")
        table.add_column("ID", style="cyan")
        table.add_column("标题")
        table.add_column("消息数", justify="right")
        table.add_column("更新时间", style="dim")
        for r in rows:
            ts = datetime.datetime.fromtimestamp(r["updated_at"]).strftime("%m-%d %H:%M")
            marker = " ←" if self.session and r["id"] == self.session.id else ""
            table.add_row(r["id"] + marker, r["title"], str(r["message_count"]), ts)
        console.print(table)

    def cmd_load(self, session_id: str):
        if not session_id:
            console.print("[red]用法: /load <session_id>[/]")
            return
        loaded = self.store.load(session_id.strip())
        if loaded is None:
            console.print(f"[red]找不到会话 {session_id}[/]")
            return
        self.session = loaded
        console.print(f"[green]已加载会话: {loaded.id} - {loaded.title} ({len(loaded.messages)} 条消息)[/]")
        # 显示最近 3 条对话作为提示
        for msg in loaded.messages[-6:]:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user":
                console.print(f"[cyan]> {content}[/]")
            elif role == "assistant" and content:
                preview = content[:100] + ("..." if len(content) > 100 else "")
                console.print(f"[green]< {preview}[/]")

    def cmd_save(self):
        if self.session:
            self.store.save(self.session)
            console.print("[dim]已保存[/]")

    def cmd_title(self, title: str):
        if not self.session or not title:
            console.print("[red]用法: /title <新标题>[/]")
            return
        self.session.title = title
        self.store.save(self.session)
        console.print(f"[dim]已重命名为: {title}[/]")

    def cmd_tools(self):
        tools = self.agent.registry.all()
        table = Table(title=f"已注册 {len(tools)} 个工具", border_style="dim")
        table.add_column("名称", style="cyan")
        table.add_column("策略", style="yellow")
        table.add_column("说明")
        for t in tools:
            decision = self.agent.approval_policy.decide(t.name).value
            desc = t.description.strip().split("\n")[0][:60]
            table.add_row(t.name, decision, desc)
        console.print(table)

    def cmd_skills(self):
        sm = self.agent.skill_manager
        if not sm or not sm.all():
            console.print("[dim](无可用 skill)[/]")
            return
        table = Table(title=f"已加载 {len(sm.all())} 个 skill", border_style="dim")
        table.add_column("名称", style="cyan")
        table.add_column("说明")
        for s in sm.all():
            desc = s.description[:80]
            table.add_row(s.name, desc)
        console.print(table)

    # ----- trace 相关命令 -----

    def _get_trace_store(self):
        ts = self.trace_store
        if ts is None and self.agent.tracer is not None:
            ts = self.agent.tracer.store
        if ts is None:
            console.print("[yellow]⚠ tracer 未启用, 无 trace 数据[/]")
            return None
        return ts

    def cmd_traces(self, arg: str):
        ts = self._get_trace_store()
        if ts is None:
            return
        try:
            n = int(arg) if arg.strip() else 10
        except ValueError:
            n = 10
        # 默认只看当前 session 的 trace; 没 session 就全部
        session_id = self.session.id if self.session else None
        list_traces(ts, console, limit=n, session_id=session_id)
        if session_id:
            console.print(f"[dim](只显示当前会话 {session_id} 的 trace, 看全部用 /traces 后无参数 + 加大 n)[/]")

    def cmd_trace(self, arg: str):
        ts = self._get_trace_store()
        if ts is None:
            return
        if not arg.strip():
            console.print("[red]用法: /trace <trace_id>[/]")
            return
        show_trace(ts, console, arg.strip())

    def cmd_span(self, arg: str):
        ts = self._get_trace_store()
        if ts is None:
            return
        if not arg.strip():
            console.print("[red]用法: /span <span_id (前 8 位即可)>[/]")
            return
        show_span(ts, console, arg.strip())

    def cmd_cost(self):
        ts = self._get_trace_store()
        if ts is None:
            return
        show_summary(ts, console)

    # ---------- 会话管理 ----------

    def _new_session(self):
        self.session = Session(
            title=f"会话 {datetime.datetime.now().strftime('%m-%d %H:%M')}",
            system_prompt=self.agent.augment_system_prompt(
                "你是一个有用的助手, 可以使用工具来回答用户问题。需要时主动调用工具, 不要瞎编。"
            ),
        )
        self.store.save(self.session)

    # ---------- 主循环 ----------

    def run_one_turn(self, user_input: str):
        """执行一轮对话, 处理事件流"""
        assert self.session is not None

        in_text_block = False  # 用于在 TextChunk 之间不断输出, 之后换行

        for event in self.agent.run_stream(self.session, user_input):
            if isinstance(event, IterationStart):
                # 第一轮不显示分隔, 后续轮次(说明在工具调用之后)显示一下
                if event.n > 1:
                    console.print(f"\n[dim yellow]--- 第 {event.n} 轮 ---[/]")
            elif isinstance(event, TextChunk):
                if not in_text_block:
                    console.print("[bold green]助手:[/] ", end="")
                    in_text_block = True
                print(event.text, end="", flush=True)
            elif isinstance(event, ToolCallStart):
                if in_text_block:
                    print()  # 文本块后换行
                    in_text_block = False
                args_preview = json.dumps(event.arguments, ensure_ascii=False)
                if len(args_preview) > 100:
                    args_preview = args_preview[:100] + "..."
                console.print(f"[magenta]→[/] [bold]{event.name}[/]({args_preview})")
            elif isinstance(event, ToolCallResult):
                tag = "[magenta]←[/]" if event.approved else "[red]✗[/]"
                preview = event.result[:300] + ("..." if len(event.result) > 300 else "")
                console.print(f"  {tag} {preview}")
                # 检查审批回调是否说"以后都允许"
                if (
                    self._approval_fn.last_decision == "always"
                    and self._approval_fn.last_tool == event.name
                ):
                    self.agent.approval_policy.always_allow.add(event.name)
                    console.print(f"  [dim]→ 本次会话不再询问 {event.name}[/]")
                    self._approval_fn.last_decision = None
            elif isinstance(event, Done):
                if in_text_block:
                    print()  # 最终回答后换行
                    in_text_block = False
                elif not event.final_text:
                    # 模型啥也没说但也没调工具(罕见)
                    console.print("[dim](模型未给出回答)[/]")
            elif isinstance(event, Aborted):
                console.print(f"[red]{event.reason}[/]")

        # 自动保存
        self.store.save(self.session)

    def main_loop(self):
        """REPL 主循环"""
        if self.session is None:
            self._new_session()

        # 欢迎语
        console.print(Panel.fit(
            "[bold cyan]my-agent REPL[/]\n"
            f"会话 ID: [yellow]{self.session.id}[/]\n"
            f"模型: [green]{self.agent.model}[/]\n"
            f"工具: {len(self.agent.registry)} 个\n"
            f"\n输入 [cyan]/help[/] 查看命令, [cyan]/quit[/] 退出",
            border_style="cyan",
        ))

        style = Style.from_dict({"prompt": "ansicyan bold"})

        while True:
            try:
                line = self.prompt.prompt(
                    HTML("<prompt>></prompt> "),
                    style=style,
                ).strip()
            except KeyboardInterrupt:
                console.print("[dim](Ctrl-C: 用 /quit 退出)[/]")
                continue
            except EOFError:
                break

            if not line:
                continue

            # 多行: 以 \ 结尾时继续接收
            while line.endswith("\\"):
                try:
                    cont = self.prompt.prompt(HTML("<prompt>...</prompt> "), style=style)
                    line = line[:-1] + "\n" + cont
                except (KeyboardInterrupt, EOFError):
                    line = line[:-1]
                    break

            # 命令优先
            try:
                if self.handle_command(line):
                    continue
            except EOFError:
                break

            # 普通输入: 走 agent
            try:
                self.run_one_turn(line)
            except Exception as e:
                console.print(f"\n[red]运行出错: {type(e).__name__}: {e}[/]")
                import traceback
                console.print(f"[dim]{traceback.format_exc()}[/]")

        console.print("[dim]再见 👋[/]")
