"""
WebUI 后端: FastAPI + SSE + 异步审批

设计:
- Agent + 业务 MCP 全局初始化一次
- 会话状态: SessionStore (SQLite)
- 流式对话: SSE
- 工具审批: 危险工具弹框确认, 通过 HTTP 回调收集决定
  - SSE 推 approval_required -> 浏览器弹框
  - 浏览器 POST /api/approvals/{id} -> Agent 线程恢复

启动:
    python -m webui.server
"""
from __future__ import annotations

import json
import os
import queue
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

load_dotenv()

from agent import Agent, Session, SessionStore  # noqa: E402
from agent.approval import ApprovalPolicy  # noqa: E402
from agent.core import (  # noqa: E402
    Aborted,
    ContextCompressed,
    Done,
    IterationStart,
    TextChunk,
    ToolCallResult,
    ToolCallStart,
)
from agent.context.budget import default_budget  # noqa: E402
from agent.context.compressor import make_default_compressor  # noqa: E402
from agent.observability import (  # noqa: E402
    RegexRedactor,
    TraceStore,
    Tracer,
    format_cost,
)
from agent.robustness import (  # noqa: E402
    CommandValidator,
    ToolResultGuard,
    guard_tool,
)
from agent.robustness.guards import make_safe_shell_tool  # noqa: E402
from agent.robustness.retry import default_mcp_retry_policy  # noqa: E402
from agent.tools.builtin import (  # noqa: E402
    calculate,
    list_dir,
    read_file,
    run_shell,
    write_file,
)
from agent.tools.mcp_client import MCPClient  # noqa: E402
from agent.tools.registry import ToolRegistry  # noqa: E402

from .approval_bridge import ApprovalBridge  # noqa: E402


# ============================================================
# 全局初始化
# ============================================================

PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = os.path.expanduser("~/.my-agent/sessions.db")
LIBRARY_DB = "/tmp/library.db"

print("[init] 准备图书馆数据...")
sys.path.insert(0, str(PROJECT_ROOT))
from mcp_servers.library.seed import seed
seed(LIBRARY_DB, reset=True)

os.environ["LIBRARY_DB_PATH"] = LIBRARY_DB
print("[init] 启动图书馆 MCP server...")
library_mcp = MCPClient(
    command=sys.executable,
    args=[str(PROJECT_ROOT / "mcp_servers" / "library" / "server.py")],
    name="library",
    retry_policy=default_mcp_retry_policy(),
    env=os.environ.copy(),
)
library_mcp.start()
print(f"[init] MCP 工具: {[t['name'] for t in library_mcp.list_tools()]}")

# 工具注册
registry = ToolRegistry()
guard = ToolResultGuard(max_chars=8000)
registry.register(guard_tool(read_file, guard))
registry.register(guard_tool(write_file, guard))  # ← write_file 也要注册(审批策略会拦)
registry.register(guard_tool(list_dir, guard))
registry.register(calculate)
registry.register(guard_tool(
    make_safe_shell_tool(run_shell, CommandValidator.default()),
    guard,
))
for mcp_tool in library_mcp.to_tools():
    registry.register(guard_tool(mcp_tool, guard))

# Tracer
trace_store = TraceStore(DB_PATH)
tracer = Tracer(store=trace_store, redactor=RegexRedactor.with_common_patterns())

# ============================================================
# 审批桥: web 场景需要异步审批
# ============================================================

approval_bridge = ApprovalBridge(timeout_seconds=120.0)

# Agent: 危险工具 + 业务"写"类工具走审批
# 业务全是查询, 实际上没"写"; 这里给 run_shell / write_file 留确认
approval_policy = ApprovalPolicy.default_safe()  # run_shell, write_file -> ASK

# approval_callback 在 send_message 里动态注入(因为要绑当前 session 的 push_event)
agent = Agent(
    registry=registry,
    approval_policy=approval_policy,
    tracer=tracer,
)

# 装上预算管理 + 历史压缩(用同一个 client 做摘要 LLM)
_compressor = make_default_compressor(agent.client, model="deepseek-chat")
agent.budget_manager = default_budget("deepseek-chat", compressor=_compressor)

session_store = SessionStore(DB_PATH)

DEFAULT_SYSTEM_PROMPT = """你是一个有用的助手, 可以使用工具完成任务。
你接入了一个图书馆查询系统, 能查询图书、读者、借阅、超期等信息。
也能读文件、列目录、执行 shell 命令、做计算。

工作风格:
- 优先用工具查询, 不要凭印象作答
- 涉及读者隐私时, MCP 返回的手机号本身已脱敏(138****8001), 不要去还原
- 回答简洁清晰, 必要时用 markdown
"""

print(f"[init] 已注册 {len(registry)} 个工具")
print(f"[init] 审批策略: 危险工具 ({sorted(approval_policy.require_ask)}) 需要确认")
print("[init] 准备完毕\n")


# ============================================================
# FastAPI 应用
# ============================================================

app = FastAPI(title="my-agent webui")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent / "static"


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


# ---------- Session API ----------

class CreateSessionReq(BaseModel):
    title: Optional[str] = None


class SessionBrief(BaseModel):
    id: str
    title: str
    created_at: float
    updated_at: float
    message_count: int


@app.get("/api/sessions")
def list_sessions() -> list[SessionBrief]:
    return [SessionBrief(**s) for s in session_store.list_sessions(limit=100)]


@app.post("/api/sessions")
def create_session(req: CreateSessionReq) -> dict:
    title = req.title or f"会话 {datetime.now().strftime('%m-%d %H:%M')}"
    s = Session(title=title, system_prompt=DEFAULT_SYSTEM_PROMPT)
    session_store.save(s)
    return {"id": s.id, "title": s.title}


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str):
    session_store.delete(session_id)
    return {"ok": True}


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str):
    s = session_store.load(session_id)
    if s is None:
        raise HTTPException(404, "session not found")
    return {
        "id": s.id,
        "title": s.title,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
        "usage": {
            "prompt_tokens": s.usage.prompt_tokens,
            "completion_tokens": s.usage.completion_tokens,
            "total_tokens": s.usage.total_tokens,
            "calls": s.usage.calls,
        },
        "turns": _messages_to_turns(s.messages),
    }


def _messages_to_turns(messages: list[dict]) -> list[dict]:
    turns = []
    current = None
    for msg in messages:
        role = msg.get("role")
        if role == "user":
            if current is not None:
                turns.append(current)
            current = {"user": msg.get("content", ""), "steps": [], "assistant": ""}
        elif role == "assistant":
            if current is None:
                continue
            content = msg.get("content", "") or ""
            tool_calls = msg.get("tool_calls") or []
            if tool_calls:
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    args_str = fn.get("arguments", "{}")
                    try:
                        args_obj = json.loads(args_str) if args_str else {}
                    except json.JSONDecodeError:
                        args_obj = {"_raw": args_str}
                    current["steps"].append({
                        "type": "tool_call",
                        "id": tc.get("id"),
                        "name": fn.get("name"),
                        "arguments": args_obj,
                    })
            else:
                current["assistant"] = content
        elif role == "tool":
            if current is None:
                continue
            tcid = msg.get("tool_call_id")
            for step in current["steps"]:
                if step.get("id") == tcid:
                    step["result"] = msg.get("content", "")
                    break
    if current is not None:
        turns.append(current)
    return turns


# ---------- 流式对话 ----------

class ChatReq(BaseModel):
    message: str


def _sse_format(event_type: str, data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event_type}\ndata: {payload}\n\n"


@app.post("/api/sessions/{session_id}/messages")
def send_message(session_id: str, req: ChatReq):
    session = session_store.load(session_id)
    if session is None:
        raise HTTPException(404, "session not found")

    # ----- 关键: 跨线程通信 -----
    # 问题: Agent.run_stream 是生成器, 在它的 next() 调用之间, 外部事件
    #       (比如 approval_required) 无法被推送 - 因为 generator 阻塞在
    #       wait/IO 上时, 我们的外层 SSE 循环也卡住了。
    # 方案: 把 Agent 跑在后台线程, 所有事件经一个 queue 流向 SSE 输出。
    #       这样审批 push_event 的事件可以在 Agent 阻塞等待时立刻到达浏览器。

    event_queue: queue.Queue = queue.Queue()
    SENTINEL = object()  # 标记流结束

    def push_event(event_type: str, data: dict):
        """approval_callback 用这个推事件给前端 (放进 queue, SSE 循环会拿)"""
        event_queue.put((event_type, data))

    def callback(tool_name: str, arguments: dict) -> tuple[bool, str]:
        return approval_bridge.await_decision(
            tool_name=tool_name,
            arguments=arguments,
            session_id=session.id,
            push_event=push_event,
        )

    # 注意: agent 全局共享, 这里 callback 会被并发请求"覆盖"
    # 学习项目演示用, 暂时接受这个限制 (生产场景要 per-request agent)
    agent.approval_callback = callback

    def agent_worker():
        """在后台线程跑 agent, 所有事件丢进 queue"""
        try:
            event_queue.put(("user_message", {"text": req.message}))

            for event in agent.run_stream(session, req.message):
                if isinstance(event, IterationStart):
                    event_queue.put(("iteration", {"n": event.n}))
                elif isinstance(event, TextChunk):
                    event_queue.put(("text", {"chunk": event.text}))
                elif isinstance(event, ToolCallStart):
                    event_queue.put(("tool_call", {
                        "id": event.call_id,
                        "name": event.name,
                        "arguments": event.arguments,
                    }))
                elif isinstance(event, ToolCallResult):
                    result = event.result
                    if len(result) > 2000:
                        result = result[:2000] + f"\n...[省略 {len(event.result) - 2000} 字]"
                    event_queue.put(("tool_result", {
                        "id": event.call_id,
                        "name": event.name,
                        "result": result,
                        "approved": event.approved,
                    }))
                elif isinstance(event, Done):
                    event_queue.put(("done", {
                        "final": event.final_text,
                        "usage": {
                            "prompt_tokens": session.usage.prompt_tokens,
                            "completion_tokens": session.usage.completion_tokens,
                            "total_tokens": session.usage.total_tokens,
                            "calls": session.usage.calls,
                        },
                    }))
                elif isinstance(event, Aborted):
                    event_queue.put(("aborted", {"reason": event.reason}))
                elif isinstance(event, ContextCompressed):
                    event_queue.put(("context_compressed", {
                        "messages_before": event.messages_before,
                        "messages_after": event.messages_after,
                        "tokens_before": event.tokens_before,
                        "tokens_after": event.tokens_after,
                    }))

            session_store.save(session)
        except Exception as e:
            import traceback
            event_queue.put(("error", {
                "type": type(e).__name__,
                "message": str(e),
                "traceback": traceback.format_exc(),
            }))
        finally:
            event_queue.put(SENTINEL)

    # 启动 agent worker
    worker = threading.Thread(target=agent_worker, daemon=True)
    worker.start()

    def stream():
        while True:
            try:
                item = event_queue.get(timeout=180.0)
            except queue.Empty:
                # 长时间没事件 - 主动断开避免连接挂死
                yield _sse_format("error", {"type": "ServerTimeout", "message": "180s 无事件"})
                return
            if item is SENTINEL:
                return
            event_type, data = item
            yield _sse_format(event_type, data)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------- 审批接口 ----------

class ApprovalDecision(BaseModel):
    approved: bool
    reason: str = ""


@app.post("/api/approvals/{approval_id}")
def resolve_approval(approval_id: str, decision: ApprovalDecision):
    """提交审批决定。Agent 线程会被唤醒。"""
    ok = approval_bridge.resolve(approval_id, decision.approved, decision.reason)
    if not ok:
        raise HTTPException(404, "approval not found or already timed out")
    return {"ok": True}


@app.get("/api/approvals/pending")
def list_pending_approvals(session_id: Optional[str] = None):
    """调试用: 看当前还在等的审批"""
    return approval_bridge.list_pending(session_id)


# ---------- 工具 / 统计 ----------

@app.get("/api/tools")
def list_tools():
    return [
        {
            "name": t.name,
            "description": t.description.strip().split("\n")[0],
            "approval": agent.approval_policy.decide(t.name).value,
        }
        for t in registry.all()
    ]


@app.get("/api/stats")
def stats():
    agg = trace_store.aggregate_cost()
    by_model = trace_store.aggregate_by_model()
    return {
        "trace_count": agg["trace_count"],
        "llm_calls": agg["llm_calls"],
        "tool_calls": agg["tool_calls"],
        "prompt_tokens": agg["prompt_tokens"],
        "completion_tokens": agg["completion_tokens"],
        "cost_usd": agg["cost_usd"],
        "cost_formatted": format_cost(agg["cost_usd"]),
        "by_model": by_model,
    }


@app.get("/api/traces")
def list_traces(session_id: Optional[str] = None, limit: int = 30):
    traces = trace_store.list_traces(limit=limit, session_id=session_id)
    return [
        {
            "id": t.id,
            "session_id": t.session_id,
            "user_input": t.user_input,
            "status": t.status.value,
            "started_at": t.started_at,
            "ended_at": t.ended_at,
            "model": t.model,
            "llm_calls": t.llm_call_count,
            "tool_calls": t.tool_call_count,
            "total_tokens": t.total_tokens,
            "cost_usd": t.total_cost_usd,
            "cost_formatted": format_cost(t.total_cost_usd),
        }
        for t in traces
    ]


# ---------- 启动 ----------

def main():
    import uvicorn
    if not os.getenv("DEEPSEEK_API_KEY"):
        print("❌ 错误: 请先在 .env 中设置 DEEPSEEK_API_KEY")
        sys.exit(1)
    port = int(os.environ.get("PORT", "8765"))
    print(f"\n🌐 启动 webui: http://localhost:{port}")
    print(f"   (用浏览器访问即可)\n")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
