"""
Tracer: 追踪记录器

设计:
- 上下文管理器 API, 用 `with` 语法自动管理 span 生命周期
- 内部维护一个 span 栈(LIFO), 嵌套自然形成父子关系
- 写入策略: 同步写(进入时插一条, 退出时 update 一条)
- 不抛异常: trace 失败不应影响主流程, 所有错误吞掉

典型用法:
    tracer = Tracer(store=trace_store)
    with tracer.start_trace(session_id="s1", user_input="hello", model="deepseek-chat") as trace:
        with trace.span(SpanKind.LLM_CALL, name="deepseek-chat") as span:
            span.set_payload("messages", messages)
            response = call_llm(...)
            span.set_attribute("prompt_tokens", response.usage.prompt_tokens)

        with trace.span(SpanKind.TOOL_CALL, name="read_file") as span:
            span.set_payload("arguments", {"path": "/tmp/x"})
            result = run_tool(...)
            span.set_payload("result", result)
"""
from __future__ import annotations

import sys
import threading
import traceback
from contextlib import contextmanager
from typing import Iterator, Optional

from .models import Span, SpanKind, SpanStatus, Trace, TraceStatus
from .pricing import estimate_cost
from .redact import NoopRedactor, Redactor
from .store import TraceStore


# ============================================================
# TraceContext: 一个正在进行的 trace, 提供 .span() 方法开新 span
# ============================================================

class TraceContext:
    """
    一次 trace 的活跃上下文。
    生命周期: tracer.start_trace() 进入时创建, 退出时 finalize。
    """

    def __init__(self, tracer: "Tracer", trace: Trace):
        self._tracer = tracer
        self.trace = trace
        # span 栈: 用于嵌套时找 parent
        # 单 trace 通常单线程, 但为防万一用 thread-local 风格的简单列表
        self._span_stack: list[Span] = []
        self._lock = threading.Lock()

    @contextmanager
    def span(
        self,
        kind: SpanKind,
        name: str,
        **attributes,
    ) -> Iterator[Span]:
        """开一个新 span, 自动 with"""
        with self._lock:
            parent = self._span_stack[-1] if self._span_stack else None

        span = Span(
            trace_id=self.trace.id,
            parent_id=parent.id if parent else None,
            kind=kind,
            name=name,
            attributes=dict(attributes),
        )

        # 进入时插一条(便于 trace 跑一半挂了也能看到)
        self._tracer._safe_save_span(span)

        with self._lock:
            self._span_stack.append(span)

        try:
            yield span
        except Exception as e:
            span.mark_error(e)
            span.attributes["traceback"] = traceback.format_exc()
            raise
        finally:
            span.finish()
            with self._lock:
                if self._span_stack and self._span_stack[-1] is span:
                    self._span_stack.pop()
            # 退出时再更新(写入 ended_at + payload + 最终 attributes)
            # 注意: Tracer 会先过一遍 redactor 再写
            self._tracer._save_span_with_redact(span)


# ============================================================
# Tracer 主对象
# ============================================================

class Tracer:
    """
    创建/管理 trace 的主对象。
    一个 Agent 通常持有一个 Tracer 实例。
    """

    def __init__(
        self,
        store: TraceStore,
        redactor: Optional[Redactor] = None,
        enabled: bool = True,
    ):
        self.store = store
        self.redactor = redactor or NoopRedactor()
        self.enabled = enabled

    @contextmanager
    def start_trace(
        self,
        user_input: str,
        session_id: str | None = None,
        model: str = "",
        **tags,
    ) -> Iterator[TraceContext]:
        """开启一个 trace。退出时聚合统计并标记完成。"""
        if not self.enabled:
            # 关闭模式: 返回一个 no-op context, 调用方代码不用改
            yield _NoopTraceContext()
            return

        trace = Trace(
            session_id=session_id,
            user_input=user_input,
            model=model,
            tags=dict(tags),
        )
        ctx = TraceContext(self, trace)

        # 先存一条(状态 running), 这样跑挂了也能看到
        self._safe_save_trace(trace)

        try:
            yield ctx
            trace.finish(TraceStatus.OK)
        except Exception as e:
            trace.finish(TraceStatus.ERROR)
            trace.tags["error"] = str(e)
            raise
        finally:
            # 聚合统计: 跑完所有 span 后从 store 拉回来算
            self._finalize_trace(trace)

    # ----- 写入辅助 (吞异常, 不影响主流程) -----

    def _safe_save_trace(self, trace: Trace) -> None:
        try:
            self.store.save_trace(trace)
        except Exception as e:
            print(f"[tracer] save_trace failed: {e}", file=sys.stderr)

    def _safe_save_span(self, span: Span) -> None:
        try:
            self.store.save_span(span)
        except Exception as e:
            print(f"[tracer] save_span failed: {e}", file=sys.stderr)

    def _save_span_with_redact(self, span: Span) -> None:
        """span 收尾时调用: 先脱敏 payload, 再写入"""
        try:
            if self.redactor and not isinstance(self.redactor, NoopRedactor):
                span.payload = self.redactor.redact(span.payload)
                span.attributes = self.redactor.redact(span.attributes)
            self.store.save_span(span)
        except Exception as e:
            print(f"[tracer] save_span_with_redact failed: {e}", file=sys.stderr)

    def _finalize_trace(self, trace: Trace) -> None:
        """聚合 spans 统计写回 trace"""
        try:
            spans = self.store.get_spans(trace.id)

            llm_calls = [s for s in spans if s.kind == SpanKind.LLM_CALL]
            tool_calls = [s for s in spans if s.kind == SpanKind.TOOL_CALL]

            trace.llm_call_count = len(llm_calls)
            trace.tool_call_count = len(tool_calls)
            trace.iteration_count = len(llm_calls)  # 每轮一次 LLM 调用

            for s in llm_calls:
                trace.total_prompt_tokens += s.attributes.get("prompt_tokens", 0) or 0
                trace.total_completion_tokens += s.attributes.get("completion_tokens", 0) or 0
                trace.total_cached_tokens += s.attributes.get("cached_tokens", 0) or 0
                trace.total_cost_usd += s.attributes.get("cost_usd", 0.0) or 0.0

            self._safe_save_trace(trace)
        except Exception as e:
            print(f"[tracer] _finalize_trace failed: {e}", file=sys.stderr)


# ============================================================
# 当 tracer.enabled=False 时返回的 no-op 实现
# ============================================================

class _NoopSpan:
    def set_attribute(self, k, v): pass
    def set_payload(self, k, v): pass
    def mark_error(self, e): pass
    def mark_cancelled(self, reason=""): pass
    def finish(self): pass


class _NoopTraceContext:
    @property
    def trace(self):
        return None

    @contextmanager
    def span(self, kind, name, **attrs):
        yield _NoopSpan()


# ============================================================
# 便捷工具: 给 LLM span 计算成本
# ============================================================

def record_llm_usage(span: Span, model: str, usage) -> None:
    """
    把 LLM 调用的 usage 信息写入 span(自动算成本)
    usage 可以是 openai 的 usage 对象, 也可以是 dict
    cache 命中字段按模型自适应读取(DeepSeek/通义/智谱/Kimi 字段名不同)
    """
    if usage is None:
        return

    if hasattr(usage, "prompt_tokens"):
        p = usage.prompt_tokens or 0
        c = usage.completion_tokens or 0
    else:
        p = usage.get("prompt_tokens", 0)
        c = usage.get("completion_tokens", 0)

    # 统一从各家 usage 字段读 cached
    from .pricing import extract_cache_info
    cached = extract_cache_info(model, usage)

    cost = estimate_cost(model, p, c, cached)

    span.set_attribute("prompt_tokens", p)
    span.set_attribute("completion_tokens", c)
    span.set_attribute("cached_tokens", cached or 0)
    span.set_attribute("cost_usd", cost)
