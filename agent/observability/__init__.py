"""
可观测性子系统

核心概念(借鉴 OpenTelemetry):
- Trace: 一次完整任务的全程记录(= 一次 agent.run)
- Span:  trace 内部的一段操作(LLM 调用 / 工具执行 / 审批 等)
- Span 之间有父子嵌套关系,形成树状结构

使用方式:
    from agent.observability import Tracer

    tracer = Tracer(store=trace_store)
    with tracer.start_trace(session_id="...", user_input="...") as trace:
        with trace.span("llm_call", model="deepseek-chat") as span:
            ...
            span.set_attribute("prompt_tokens", 100)
"""
from .models import Span, SpanKind, SpanStatus, Trace, TraceStatus
from .pricing import Pricing, estimate_cost, format_cost, register_pricing
from .redact import (
    ChainRedactor,
    NoopRedactor,
    Redactor,
    RegexRedactor,
    TruncateRedactor,
)
from .store import TraceStore
from .tracer import Tracer, record_llm_usage
from .viewer import list_traces, show_span, show_summary, show_trace

__all__ = [
    "Tracer", "record_llm_usage",
    "TraceStore",
    "Trace", "TraceStatus",
    "Span", "SpanKind", "SpanStatus",
    "Pricing", "estimate_cost", "format_cost", "register_pricing",
    "Redactor", "NoopRedactor", "RegexRedactor", "TruncateRedactor", "ChainRedactor",
    "list_traces", "show_trace", "show_span", "show_summary",
]
