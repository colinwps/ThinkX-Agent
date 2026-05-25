"""
Agent 主循环 —— v2: 会话化 + 流式 + 工具审批

设计变化:
- Agent 自己不存 messages, 而是操作传入的 Session
- 同一个 Agent 实例可以服务多个 Session(并发安全, 因为不持有状态)
- run() 同步阻塞接口; run_stream() 流式接口
- 工具调用前过 approval 钩子(没传就默认全放行)
"""
from __future__ import annotations

import json
import os
from typing import Iterator

from openai import OpenAI
from rich import print as rprint
from rich.panel import Panel

from .approval import ApprovalCallback, ApprovalPolicy, Decision, auto_approve
from .session import Session
from .skills.manager import SkillManager
from .streaming import consume_stream
from .tools.base import Tool
from .tools.registry import ToolRegistry


# 流式事件类型 —— Agent 把执行过程作为事件流推给调用方
# 调用方(CLI 或 Web)按事件类型决定怎么显示
class Event:
    """流式执行过程中的事件基类"""


class TextChunk(Event):
    def __init__(self, text: str):
        self.text = text


class ToolCallStart(Event):
    def __init__(self, name: str, arguments: dict, call_id: str):
        self.name = name
        self.arguments = arguments
        self.call_id = call_id


class ToolCallResult(Event):
    def __init__(self, name: str, call_id: str, result: str, approved: bool):
        self.name = name
        self.call_id = call_id
        self.result = result
        self.approved = approved


class IterationStart(Event):
    def __init__(self, n: int):
        self.n = n


class Done(Event):
    def __init__(self, final_text: str):
        self.final_text = final_text


class Aborted(Event):
    def __init__(self, reason: str):
        self.reason = reason


# ============================================================
# Agent
# ============================================================

class Agent:
    def __init__(
        self,
        registry: ToolRegistry,
        skill_manager: SkillManager | None = None,
        approval_policy: ApprovalPolicy | None = None,
        approval_callback: ApprovalCallback | None = None,
        model: str = "deepseek-chat",
        base_url: str = "https://api.deepseek.com/v1",
        api_key: str | None = None,
        max_iterations: int = 15,
    ):
        self.registry = registry
        self.skill_manager = skill_manager
        self.approval_policy = approval_policy or ApprovalPolicy()
        self.approval_callback = approval_callback or auto_approve
        self.model = model
        self.max_iterations = max_iterations

        if skill_manager is not None and not registry.get("load_skill"):
            self._install_skill_support()

        self.client = OpenAI(
            api_key=api_key or os.getenv("DEEPSEEK_API_KEY"),
            base_url=base_url,
        )

    # ---------- skill 集成(沿用 v1) ----------

    def _install_skill_support(self):
        sm = self.skill_manager
        assert sm is not None

        def load_skill(name: str) -> str:
            """根据 skill 名加载完整的 skill 内容, 看完后请按其中指引行动。"""
            return sm.load_skill_content(name)

        skill_names = ", ".join(sm.skills.keys()) or "(暂无)"
        tool = Tool.__new__(Tool)
        tool.name = "load_skill"
        tool.description = (
            f"加载一个 skill 的完整内容。当任务匹配 system prompt 中列出的某个 skill 时, "
            f"先调用本工具拿到完整指引, 再按指引完成任务。"
            f"当前可用 skill: {skill_names}"
        )
        tool.parameters_schema = {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "skill 名称"}},
            "required": ["name"],
        }
        tool.func = load_skill
        self.registry.register(tool)

    def augment_system_prompt(self, base: str) -> str:
        """把 skill catalog 拼到 system prompt 后面"""
        if self.skill_manager is not None:
            return base + self.skill_manager.render_catalog()
        return base

    # ---------- 工具执行(含审批) ----------

    def _execute_tool(self, name: str, arguments: dict) -> tuple[str, bool]:
        """
        执行一个工具, 返回 (结果文本, 是否真的执行了)

        审批流程:
        1. 查策略 -> ALLOW/DENY/ASK
        2. ASK 时调用 approval_callback 问用户
        3. 被拒绝时返回拒绝信息(模型会看到)
        """
        decision = self.approval_policy.decide(name)

        if decision == Decision.DENY:
            return f"[已拒绝] 工具 {name} 被策略禁止执行。", False

        if decision == Decision.ASK:
            approved, reason = self.approval_callback(name, arguments)
            if not approved:
                msg = f"[用户拒绝] 工具 {name} 未被执行。"
                if reason:
                    msg += f" 理由: {reason}"
                return msg, False

        # ALLOW 或 ASK 通过 -> 真正执行
        result = self.registry.execute(name, arguments)
        return result, True

    # ---------- 同步接口 ----------

    def run(self, session: Session, user_input: str, verbose: bool = True) -> str:
        """
        非流式: 一次性返回最终回答。
        verbose=True 时打印过程, False 时静默(适合脚本调用)。
        """
        events = self.run_stream(session, user_input)
        final = ""

        for event in events:
            if not verbose:
                continue
            if isinstance(event, IterationStart):
                rprint(f"\n[bold yellow]--- 第 {event.n} 轮 ---[/]")
            elif isinstance(event, TextChunk):
                print(event.text, end="", flush=True)
            elif isinstance(event, ToolCallStart):
                rprint(f"\n  [magenta]→[/] [bold]{event.name}[/]({event.arguments})")
            elif isinstance(event, ToolCallResult):
                tag = "[magenta]←[/]" if event.approved else "[red]✗[/]"
                preview = event.result[:200] + ("..." if len(event.result) > 200 else "")
                rprint(f"  {tag} {preview}")
            elif isinstance(event, Done):
                final = event.final_text
                rprint(Panel(f"[bold green]助手[/]:\n{final}", border_style="green"))
            elif isinstance(event, Aborted):
                rprint(f"[red]{event.reason}[/]")

        return final

    # ---------- 流式接口(核心) ----------

    def run_stream(self, session: Session, user_input: str) -> Iterator[Event]:
        """
        流式执行:
        - 把 user_input 加入 session
        - 进入 ReAct 循环, 把每一步作为 Event 推出来
        - 调用方(CLI/Web)按 Event 类型决定怎么呈现

        这个函数是 generator, 每 yield 一个 Event 就阻塞等下一次 next()。
        """
        session.add_user(user_input)

        # 每次 run 都重建 system prompt(支持运行时切换 skill / prompt)
        if not session.system_prompt:
            session.system_prompt = self.augment_system_prompt(
                "你是一个有用的助手, 可以使用工具来回答用户问题。需要时主动调用工具, 不要瞎编。"
            )

        for iteration in range(self.max_iterations):
            yield IterationStart(iteration + 1)

            # ----- 调 LLM(流式) -----
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=session.to_llm_messages(),
                tools=self.registry.to_openai_schemas(),
                tool_choice="auto",
                stream=True,
                stream_options={"include_usage": True},  # 让最后一个 chunk 带 usage
            )

            # 累加 chunks, 同时缓冲文字片段(在循环结束后一次性 yield)
            # 之所以缓冲而不是边收边 yield, 是为了不让生成器的暂停干扰 stream 消费
            text_buffer: list[str] = []

            def on_text(piece: str):
                text_buffer.append(piece)

            streamed = consume_stream(stream, on_text=on_text)

            # 把累加期间收到的文字一次性 yield
            for piece in text_buffer:
                yield TextChunk(piece)

            # 更新会话状态
            session.usage.add(streamed.usage)
            session.add_message(streamed.to_message_dict())

            # ----- 情况 A: 没有工具调用, 流程结束 -----
            if not streamed.tool_calls:
                yield Done(streamed.content)
                return

            # ----- 情况 B: 有工具调用, 逐个处理(含审批) -----
            for tc in streamed.tool_calls:
                try:
                    args = json.loads(tc.arguments) if tc.arguments else {}
                except json.JSONDecodeError as e:
                    result = f"[错误] 参数不是合法 JSON: {e}"
                    yield ToolCallStart(tc.name, {}, tc.id)
                    yield ToolCallResult(tc.name, tc.id, result, approved=False)
                    session.add_message({
                        "role": "tool", "tool_call_id": tc.id, "content": result,
                    })
                    continue

                yield ToolCallStart(tc.name, args, tc.id)
                result, approved = self._execute_tool(tc.name, args)
                yield ToolCallResult(tc.name, tc.id, result, approved=approved)

                session.add_message({
                    "role": "tool", "tool_call_id": tc.id, "content": result,
                })

        yield Aborted(f"达到最大轮次 ({self.max_iterations}), 强制结束")
