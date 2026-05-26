"""
Trace / Span 数据模型

设计参考 OpenTelemetry, 但简化为我们的场景:
- 不做分布式跟踪(单进程, 不需要 trace propagation)
- attributes 直接 dict (不区分 string/int/array)
- 时间戳用 float epoch seconds (够用了)
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ============================================================
# Span 类型 —— 限定枚举, 便于查询统计
# ============================================================

class SpanKind(str, Enum):
    AGENT_RUN = "agent_run"      # 一次 agent.run 的根 span
    LLM_CALL = "llm_call"        # 一次 LLM API 调用
    TOOL_CALL = "tool_call"      # 一次工具执行
    APPROVAL = "approval"        # 一次审批询问
    ERROR = "error"              # 异常事件
    OTHER = "other"              # 自定义扩展


class SpanStatus(str, Enum):
    OK = "ok"
    ERROR = "error"
    CANCELLED = "cancelled"      # 用户中断/拒绝


class TraceStatus(str, Enum):
    RUNNING = "running"
    OK = "ok"
    ERROR = "error"
    CANCELLED = "cancelled"


# ============================================================
# Span: 一段操作的记录
# ============================================================

@dataclass
class Span:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    trace_id: str = ""
    parent_id: Optional[str] = None
    kind: SpanKind = SpanKind.OTHER
    name: str = ""               # 人类可读的名字, 如 "deepseek-chat" 或 "read_file"
    status: SpanStatus = SpanStatus.OK

    started_at: float = field(default_factory=time.time)
    ended_at: Optional[float] = None

    # 自由 attributes —— 不同 kind 写不同字段
    # 比如 LLM_CALL 写 prompt_tokens/completion_tokens/cost
    # TOOL_CALL 写 arguments/result/approved
    attributes: dict[str, Any] = field(default_factory=dict)

    # 大字段单独存(便于查询时 lazy 加载)
    # 比如 prompt/response/tool_result —— 全部 JSON 序列化后存
    payload: dict[str, Any] = field(default_factory=dict)

    error: Optional[str] = None  # 错误简述, 详情放 attributes

    @property
    def duration_ms(self) -> Optional[float]:
        if self.ended_at is None:
            return None
        return (self.ended_at - self.started_at) * 1000

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def set_payload(self, key: str, value: Any) -> None:
        self.payload[key] = value

    def mark_error(self, error: str | Exception) -> None:
        self.status = SpanStatus.ERROR
        self.error = str(error) if isinstance(error, Exception) else error

    def mark_cancelled(self, reason: str = "") -> None:
        self.status = SpanStatus.CANCELLED
        if reason:
            self.attributes["cancel_reason"] = reason

    def finish(self) -> None:
        if self.ended_at is None:
            self.ended_at = time.time()


# ============================================================
# Trace: 一次完整任务的容器
# ============================================================

@dataclass
class Trace:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    session_id: Optional[str] = None  # 关联的会话(可空)
    user_input: str = ""
    final_output: str = ""
    status: TraceStatus = TraceStatus.RUNNING

    started_at: float = field(default_factory=time.time)
    ended_at: Optional[float] = None

    # 模型 / 元信息
    model: str = ""

    # 跨 span 聚合的统计 —— 写完所有 span 后由 Tracer 计算填入
    iteration_count: int = 0
    llm_call_count: int = 0
    tool_call_count: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_cached_tokens: int = 0
    total_cost_usd: float = 0.0

    # 自由元信息
    tags: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> Optional[float]:
        if self.ended_at is None:
            return None
        return (self.ended_at - self.started_at) * 1000

    @property
    def total_tokens(self) -> int:
        return self.total_prompt_tokens + self.total_completion_tokens

    def finish(self, status: TraceStatus = TraceStatus.OK) -> None:
        if self.ended_at is None:
            self.ended_at = time.time()
        self.status = status
