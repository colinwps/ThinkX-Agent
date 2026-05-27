"""
Web 端的审批桥接器

问题:
- Agent.run_stream 在工作线程里跑, 遇到要审批的工具会调 approval_callback
- 这个 callback 必须阻塞等用户决定
- 用户的决定来自另一个 HTTP 请求 (POST /api/approvals/{id})

解决:
- 一个全局 ApprovalBridge: 维护"待审批请求"字典 + 每个请求一个 Event 信号
- approval_callback 创建 pending 项 + 推送 SSE + 阻塞 wait
- HTTP 接口收到决定后 set 那个 Event, 让 callback 返回

线程安全:
- pending 字典操作用 Lock
- Event 是 threading 原生类型, 自带同步
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class _PendingApproval:
    """一个等待中的审批请求"""
    id: str
    tool_name: str
    arguments: dict
    session_id: str
    # 通信
    event: threading.Event = field(default_factory=threading.Event)
    # 结果
    approved: bool = False
    reason: str = ""
    # 是否已超时
    timed_out: bool = False


class ApprovalBridge:
    """
    Web 场景的审批桥接。

    使用流程:
    1. Agent 调 await_decision(tool_name, args, session_id, push_event)
       -> 内部生成一个 pending, 用 push_event 把 'approval_required' 推到 SSE
       -> 阻塞等
    2. 前端弹框, 用户决定, POST /api/approvals/{id} {approved, reason}
       -> 调 resolve(id, approved, reason)
       -> 触发 Event, await_decision 返回
    """

    def __init__(self, timeout_seconds: float = 120.0):
        self.timeout = timeout_seconds
        self._pending: dict[str, _PendingApproval] = {}
        self._lock = threading.Lock()

    def await_decision(
        self,
        tool_name: str,
        arguments: dict,
        session_id: str,
        push_event: Callable[[str, dict], None],
    ) -> tuple[bool, str]:
        """
        阻塞等待审批决定。

        push_event: 一个回调, 用来把审批请求推给前端
                    (典型实现: 往 SSE 队列里塞)
        """
        approval_id = uuid.uuid4().hex[:12]
        item = _PendingApproval(
            id=approval_id,
            tool_name=tool_name,
            arguments=arguments,
            session_id=session_id,
        )

        with self._lock:
            self._pending[approval_id] = item

        try:
            # 1. 通知前端
            push_event("approval_required", {
                "id": approval_id,
                "tool_name": tool_name,
                "arguments": arguments,
                "timeout": self.timeout,
            })

            # 2. 阻塞等
            signaled = item.event.wait(timeout=self.timeout)

            if not signaled:
                item.timed_out = True
                push_event("approval_timeout", {"id": approval_id})
                return False, f"审批超时 ({self.timeout:.0f}s 无响应)"

            return item.approved, item.reason

        finally:
            with self._lock:
                self._pending.pop(approval_id, None)

    def resolve(self, approval_id: str, approved: bool, reason: str = "") -> bool:
        """
        外部(HTTP 接口)调用: 提交某个 pending 的决定。
        返回 True 表示找到了并通知成功; False 表示已不存在(可能超时了)
        """
        with self._lock:
            item = self._pending.get(approval_id)
            if item is None or item.timed_out:
                return False
            item.approved = approved
            item.reason = reason

        item.event.set()
        return True

    def list_pending(self, session_id: Optional[str] = None) -> list[dict]:
        """调试用: 看当前还在等的审批"""
        with self._lock:
            items = list(self._pending.values())
        if session_id:
            items = [i for i in items if i.session_id == session_id]
        return [
            {
                "id": i.id,
                "tool_name": i.tool_name,
                "arguments": i.arguments,
                "session_id": i.session_id,
            }
            for i in items
        ]
