"""
Agent 主循环 —— v6: + 上下文管理(prompt cache + 历史压缩 + 预算)

设计变化:
- Agent 自己不存 messages, 而是操作传入的 Session
- 同一个 Agent 实例可以服务多个 Session(并发安全, 因为不持有状态)
- run() 同步阻塞接口; run_stream() 流式接口
- 工具调用前过 approval 钩子(没传就默认全放行)
- 关键节点埋点写入 Tracer(可选, 不传就完全 no-op)
- 调 LLM 前过 budget 检查, 必要时压缩历史
"""
from __future__ import annotations

import json
import os
from typing import Iterator, Optional

from openai import OpenAI
from rich import print as rprint
from rich.panel import Panel

from .approval import ApprovalCallback, ApprovalPolicy, Decision, auto_approve
from .context.budget import BudgetManager
from .observability.models import SpanKind
from .observability.tracer import Tracer, record_llm_usage
from .parallel import execute_tools_parallel
from .robustness.llm_client import ResilientOpenAI
from .robustness.retry import RetryPolicy
from .session import Session
from .skills.manager import SkillManager
from .strategies.plan import Planner, PlanResult
from .strategies.reflect import Reflector, ReflectionResult
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


class ContextCompressed(Event):
    """触发了历史压缩"""
    def __init__(self, messages_before: int, messages_after: int,
                 tokens_before: int, tokens_after: int):
        self.messages_before = messages_before
        self.messages_after = messages_after
        self.tokens_before = tokens_before
        self.tokens_after = tokens_after


class PlanGenerated(Event):
    """生成了执行计划 (A4 Plan-and-Execute)"""
    def __init__(self, steps: list[str], raw_response: str = ""):
        self.steps = steps
        self.raw_response = raw_response


class ReflectionDone(Event):
    """反思评估完成 (A5 Reflection)"""
    def __init__(self, score: int, suggestions: str, retry_triggered: bool):
        self.score = score
        self.suggestions = suggestions
        self.retry_triggered = retry_triggered


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
        tracer: Tracer | None = None,
        retry_policy: RetryPolicy | None = None,
        budget_manager: Optional[BudgetManager] = None,
        parallel_tools: bool = False,
        planner: Optional[Planner] = None,
        plan_first: bool = False,
        reflector: Optional[Reflector] = None,
        reflect: bool = False,
        reflect_threshold: int = 7,
        model: str = "deepseek-chat",
        base_url: str = "https://api.deepseek.com/v1",
        api_key: str | None = None,
        max_iterations: int = 15,
    ):
        self.registry = registry
        self.skill_manager = skill_manager
        self.approval_policy = approval_policy or ApprovalPolicy()
        self.approval_callback = approval_callback or auto_approve
        self.tracer = tracer
        self.budget_manager = budget_manager
        self.parallel_tools = parallel_tools

        # Plan-and-Execute
        self.planner = planner
        self.plan_first = plan_first

        # Reflection
        self.reflector = reflector
        self.reflect = reflect
        self.reflect_threshold = reflect_threshold

        self.model = model
        self.max_iterations = max_iterations

        if skill_manager is not None and not registry.get("load_skill"):
            self._install_skill_support()

        # 用 ResilientOpenAI(retry 装饰过的) 替代裸 OpenAI
        self.client = ResilientOpenAI(
            api_key=api_key or os.getenv("DEEPSEEK_API_KEY"),
            base_url=base_url,
            retry_policy=retry_policy,
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

        实现上分两层:
        - 外层在这里管 trace 生命周期(with 上下文管理器)
        - 内层 _run_stream_inner 是纯执行逻辑(接收一个 trace_ctx)
        这么做是为了让 generator 和 context manager 能正确配合(yield 不能跨越 with 边界)
        """
        if self.tracer is None:
            # 没装 tracer 的情况: 给一个 noop 上下文, 代码不用改
            from .observability.tracer import _NoopTraceContext
            yield from self._run_stream_inner(
                session, user_input, trace_ctx=_NoopTraceContext()
            )
            return

        # 装了 tracer: 包一层 trace 上下文
        # 关键: yield from 在 with 块内, with 退出会在 generator 关闭时自动触发
        final_output_holder: list[str] = []
        with self.tracer.start_trace(
            user_input=user_input,
            session_id=session.id,
            model=self.model,
        ) as trace_ctx:
            try:
                for event in self._run_stream_inner(session, user_input, trace_ctx):
                    if isinstance(event, Done):
                        final_output_holder.append(event.final_text)
                    yield event
            finally:
                # 回写最终输出到 trace(注意: 此时 tracer 还没 finalize, 这条会被存进去)
                if trace_ctx.trace is not None and final_output_holder:
                    trace_ctx.trace.final_output = final_output_holder[0]

    def _run_stream_inner(
        self,
        session: Session,
        user_input: str,
        trace_ctx,  # TraceContext | _NoopTraceContext
    ) -> Iterator[Event]:
        """run_stream 的内层实现, 不管 trace 生命周期"""
        session.add_user(user_input)

        # 每次 run 都重建 system prompt(支持运行时切换 skill / prompt)
        base_system = (
            "你是一个有用的助手, 可以使用工具来回答用户问题。需要时主动调用工具, 不要瞎编。"
        )

        # ----- A4: Plan-and-Execute (可选, 在 ReAct 循环前) -----
        plan_text = ""
        if self.plan_first and self.planner is not None:
            tools_summary = self._render_tools_summary()
            with trace_ctx.span(SpanKind.LLM_CALL, name="planner") as ps:
                ps.set_payload("user_task", user_input)
                plan_result = self.planner.plan(user_input, tools_summary)
                ps.set_payload("plan_steps", plan_result.steps)
                ps.set_attribute("valid", plan_result.is_valid)
                if plan_result.parse_error:
                    ps.set_attribute("parse_error", plan_result.parse_error)

            if plan_result.is_valid:
                yield PlanGenerated(plan_result.steps, plan_result.raw_response)
                plan_text = plan_result.render_for_system()

        if not session.system_prompt:
            session.system_prompt = self.augment_system_prompt(base_system)

        # 把 plan 临时追加到 system_prompt (只这一轮有效)
        effective_system = session.system_prompt
        if plan_text:
            effective_system = effective_system + "\n\n" + plan_text

        # 执行主循环 - 用一个内部 helper, 因为我们要复用它做 reflection retry
        final_answer = ""
        for evt in self._react_loop(session, trace_ctx, effective_system):
            if isinstance(evt, Done):
                final_answer = evt.final_text
            yield evt

        # ----- A5: Reflection (可选, 给完答案后) -----
        if not self.reflect or self.reflector is None or not final_answer:
            return

        with trace_ctx.span(SpanKind.LLM_CALL, name="reflector") as rs:
            rs.set_payload("user_input", user_input)
            rs.set_payload("assistant_answer", final_answer)
            reflection = self.reflector.evaluate(user_input, final_answer)
            rs.set_attribute("score", reflection.score)
            rs.set_attribute("valid", reflection.is_valid)
            if reflection.suggestions:
                rs.set_payload("suggestions", reflection.suggestions)

        needs_retry = reflection.needs_retry(self.reflect_threshold)
        yield ReflectionDone(
            score=reflection.score,
            suggestions=reflection.suggestions,
            retry_triggered=needs_retry,
        )

        if not needs_retry:
            return

        # 触发重答 - 把反思建议作为新的用户消息塞进去
        retry_prompt = (
            f"[内部反思: 之前的回答得分 {reflection.score}/10]\n"
            f"改进建议: {reflection.suggestions}\n\n"
            f"请基于这些建议, 重新更好地回答原问题。不要解释, 直接给改进后的回答。"
        )
        session.add_user(retry_prompt)
        for evt in self._react_loop(session, trace_ctx, effective_system):
            yield evt

    def _react_loop(
        self,
        session: Session,
        trace_ctx,
        effective_system: str | None = None,
    ) -> Iterator[Event]:
        """ReAct 主循环。
        effective_system: 临时覆盖 system_prompt(plan 模式下注入 plan)。
        None 表示用 session.system_prompt。
        """
        for iteration in range(self.max_iterations):
            yield IterationStart(iteration + 1)

            # ----- 预算检查 + 必要时压缩历史 -----
            messages_snapshot = session.to_llm_messages(system_override=effective_system)
            if self.budget_manager is not None:
                new_msgs, check = self.budget_manager.check_and_compress(messages_snapshot)
                if check.compressed and check.compression is not None:
                    # 真的发生了压缩 -> 把压缩后的 messages 写回 session
                    # 注意: 摘要被插在了开头(system 角色), 原 system_prompt 被替代
                    # 为保留原 system_prompt, 我们手动恢复
                    summary_msg = new_msgs[0]  # 压缩生成的摘要 (system)
                    rest = new_msgs[1:]         # 保留的最近 N 条
                    # 重建 session.messages: 摘要 + rest
                    # session.system_prompt 仍单独保留, 会在 to_llm_messages 时拼回最前
                    session.messages = [summary_msg] + rest
                    messages_snapshot = session.to_llm_messages(system_override=effective_system)

                    yield ContextCompressed(
                        messages_before=check.compression.messages_before,
                        messages_after=check.compression.messages_after,
                        tokens_before=check.compression.tokens_before,
                        tokens_after=check.compression.tokens_after,
                    )

            # ----- 调 LLM(流式) + LLM_CALL span -----
            with trace_ctx.span(
                SpanKind.LLM_CALL,
                name=self.model,
                iteration=iteration + 1,
            ) as llm_span:
                # 记录 prompt(进 payload, 会被 redactor 处理)
                llm_span.set_payload("messages", messages_snapshot)
                llm_span.set_attribute("estimated_tokens",
                    self.budget_manager.estimate(messages_snapshot) if self.budget_manager else 0)

                stream = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages_snapshot,
                    tools=self.registry.to_openai_schemas(),
                    tool_choice="auto",
                    stream=True,
                    stream_options={"include_usage": True},
                )

                text_buffer: list[str] = []

                def on_text(piece: str):
                    text_buffer.append(piece)

                streamed = consume_stream(stream, on_text=on_text)

                # 记录响应到 payload + 统计到 attributes
                llm_span.set_payload("response_content", streamed.content)
                llm_span.set_payload(
                    "tool_calls",
                    [
                        {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                        for tc in streamed.tool_calls
                    ],
                )
                llm_span.set_attribute("finish_reason", streamed.finish_reason or "")
                record_llm_usage(llm_span, self.model, streamed.usage)

            # 把累加期间收到的文字一次性 yield
            for piece in text_buffer:
                yield TextChunk(piece)

            # 更新会话状态
            session.usage.add(streamed.usage, model=self.model)
            session.add_message(streamed.to_message_dict())

            # ----- 情况 A: 没有工具调用, 流程结束 -----
            if not streamed.tool_calls:
                yield Done(streamed.content)
                return

            # ----- 情况 B: 有工具调用, 串行 OR 并行 -----
            # 先解析所有参数 + yield ToolCallStart 事件(顺序保留)
            parsed_calls = []  # 每项: (tc, args, parse_error)
            for tc in streamed.tool_calls:
                try:
                    args = json.loads(tc.arguments) if tc.arguments else {}
                    parse_error = None
                except json.JSONDecodeError as e:
                    args = {}
                    parse_error = str(e)
                parsed_calls.append((tc, args, parse_error))
                yield ToolCallStart(tc.name, args, tc.id)

            # 真正执行: 串行 OR 并行
            if not self.parallel_tools or len(parsed_calls) <= 1:
                # ----- 串行路径 (默认) -----
                for tc, args, parse_error in parsed_calls:
                    result, approved = self._execute_one_with_span(
                        tc.name, tc.id, args, parse_error, trace_ctx,
                    )
                    yield ToolCallResult(tc.name, tc.id, result, approved=approved)
                    session.add_message({
                        "role": "tool", "tool_call_id": tc.id, "content": result,
                    })
            else:
                # ----- 并行路径 -----
                # 把"含 span 的执行"包成 executor_fn
                # 注意: 并行下每个工具的 span 仍是 trace_run 的直接子节点
                #       (TOOL_CALL span 不互相嵌套, 与串行行为一致)

                # 提前算出哪些有 parse_error - 这些不进 executor, 直接错误返回
                # 并行执行只针对参数 OK 的
                from .parallel import execute_tools_parallel

                ok_calls = [(tc, args) for tc, args, pe in parsed_calls if pe is None]
                err_calls = {tc.id: pe for tc, _, pe in parsed_calls if pe}

                # 构造 (call_id, name, args) 三元组
                triplets = [(tc.id, tc.name, args) for tc, args in ok_calls]

                def executor_fn(name: str, args: dict) -> tuple[str, bool]:
                    """每个工具的执行: 审批 + 执行 + 写 span"""
                    # 在并行线程里开 span - 注意 trace_ctx 是上下文管理器 wrapper
                    # 我们的 trace_ctx.span 是支持线程安全的(spans 各自独立)
                    with trace_ctx.span(
                        SpanKind.TOOL_CALL,
                        name=name,
                    ) as tool_span:
                        tool_span.set_payload("arguments", args)
                        result, approved = self._execute_tool_traced(
                            name, args, trace_ctx,
                        )
                        tool_span.set_payload("result", result)
                        tool_span.set_attribute("approved", approved)
                        if not approved:
                            tool_span.mark_cancelled("not approved")
                        return result, approved

                def needs_approval(name: str) -> bool:
                    return self.approval_policy.decide(name) == Decision.ASK

                results = execute_tools_parallel(
                    triplets, executor_fn, needs_approval, max_workers=4,
                )

                # 按原 parsed_calls 顺序 yield + 追加 session.messages
                # parse_error 的提前发, 不会丢顺序
                result_map = {r.call_id: r for r in results}
                for tc, args, parse_error in parsed_calls:
                    if parse_error is not None:
                        result_str = f"[错误] 参数不是合法 JSON: {parse_error}"
                        approved = False
                        # 给一个 error span
                        with trace_ctx.span(SpanKind.TOOL_CALL, name=tc.name) as s:
                            s.set_payload("arguments", args)
                            s.set_payload("result", result_str)
                            s.mark_error(f"json decode: {parse_error}")
                    else:
                        r = result_map[tc.id]
                        result_str = r.result
                        approved = r.approved

                    yield ToolCallResult(tc.name, tc.id, result_str, approved=approved)
                    session.add_message({
                        "role": "tool", "tool_call_id": tc.id, "content": result_str,
                    })

        yield Aborted(f"达到最大轮次 ({self.max_iterations}), 强制结束")

    def _render_tools_summary(self) -> str:
        """简短列出工具名 + 一句描述, 给 planner 看"""
        lines = []
        for t in self.registry.all():
            first_line = t.description.strip().split("\n")[0][:80]
            lines.append(f"- {t.name}: {first_line}")
        return "\n".join(lines)

    def _execute_one_with_span(
        self, name: str, call_id: str, args: dict, parse_error: str | None, trace_ctx,
    ) -> tuple[str, bool]:
        """单个工具执行 + span 包装(串行路径用)"""
        with trace_ctx.span(
            SpanKind.TOOL_CALL,
            name=name,
            tool_call_id=call_id,
        ) as tool_span:
            tool_span.set_payload("arguments", args)
            if parse_error:
                result = f"[错误] 参数不是合法 JSON: {parse_error}"
                approved = False
                tool_span.mark_error(f"json decode: {parse_error}")
            else:
                result, approved = self._execute_tool_traced(
                    name, args, trace_ctx,
                )
            tool_span.set_payload("result", result)
            tool_span.set_attribute("approved", approved)
            if not approved:
                tool_span.mark_cancelled("not approved")
            return result, approved

    def _execute_tool_traced(
        self, name: str, arguments: dict, trace_ctx,
    ) -> tuple[str, bool]:
        """带 APPROVAL span 的工具执行"""
        decision = self.approval_policy.decide(name)

        if decision == Decision.DENY:
            with trace_ctx.span(SpanKind.APPROVAL, name=f"deny:{name}") as s:
                s.set_attribute("decision", "deny_by_policy")
            return f"[已拒绝] 工具 {name} 被策略禁止执行。", False

        if decision == Decision.ASK:
            with trace_ctx.span(SpanKind.APPROVAL, name=f"ask:{name}") as s:
                approved, reason = self.approval_callback(name, arguments)
                s.set_attribute("decision", "approved" if approved else "rejected")
                if reason:
                    s.set_payload("user_reason", reason)
            if not approved:
                msg = f"[用户拒绝] 工具 {name} 未被执行。"
                if reason:
                    msg += f" 理由: {reason}"
                return msg, False

        # 真正执行
        result = self.registry.execute(name, arguments)
        return result, True
