"""
Session: 一次连续对话的状态容器

职责:
1. 维护 messages 历史
2. 累计 token / 成本统计
3. 持久化到 SQLite(可选)
4. 提供 fork / clear / load / save 等操作

设计要点:
- Session 是数据容器, 不包含业务逻辑(不调 LLM、不执行工具)
- Agent 操作 Session, 而不是反过来 —— Agent 无状态, Session 有状态
- 一个 Session 对应一段连续对话(可能跨多次 run)
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ============================================================
# Token 与成本统计
# ============================================================

@dataclass
class TokenUsage:
    """累积的 token 用量"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # 国内模型大部分支持 cache, 单独计数
    cached_tokens: int = 0
    calls: int = 0  # LLM 调用次数

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add(self, usage: Any, model: str = "") -> None:
        """从 LLM 响应的 usage 字段累加。model 用于按模型分发读 cache 字段。"""
        if usage is None:
            return
        self.calls += 1
        # OpenAI SDK 的 usage 对象字段
        self.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
        self.completion_tokens += getattr(usage, "completion_tokens", 0) or 0

        # 多模型 cache 字段统一读取
        if model:
            from .observability.pricing import extract_cache_info
            self.cached_tokens += extract_cache_info(model, usage)
        else:
            # 没 model 时退化到通用字段
            details = getattr(usage, "prompt_tokens_details", None)
            if details is not None:
                cached = getattr(details, "cached_tokens", 0) or 0
                self.cached_tokens += cached

    def summary(self) -> str:
        cache_info = f", cached: {self.cached_tokens}" if self.cached_tokens else ""
        return (
            f"calls: {self.calls}, "
            f"in: {self.prompt_tokens}, out: {self.completion_tokens}"
            f"{cache_info}, total: {self.total_tokens}"
        )


# ============================================================
# Session
# ============================================================

@dataclass
class Session:
    """
    一次会话的状态。
    messages 直接存 OpenAI 格式的 dict, 便于序列化和直接传给 LLM。
    """
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    title: str = "未命名会话"
    system_prompt: str = ""
    messages: list[dict] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    # ----- 消息操作 -----

    def to_llm_messages(self, system_override: str | None = None) -> list[dict]:
        """
        生成传给 LLM 的 messages 列表(system + history)
        每次都重建, 因为 system_prompt 可能被外部更新(比如 skill catalog)
        system_override: 临时覆盖 system_prompt(plan 阶段用), 不写回 session
        """
        msgs = []
        sp = system_override if system_override is not None else self.system_prompt
        if sp:
            msgs.append({"role": "system", "content": sp})
        msgs.extend(self.messages)
        return msgs

    def add_message(self, message: dict) -> None:
        """追加一条消息(user / assistant / tool)"""
        self.messages.append(message)
        self.updated_at = time.time()

    def add_user(self, content: str) -> None:
        self.add_message({"role": "user", "content": content})

    def clear(self, keep_system: bool = True) -> None:
        """清空历史。keep_system=True 时只清 user/assistant/tool, 保留 system_prompt"""
        self.messages = []
        if not keep_system:
            self.system_prompt = ""
        self.usage = TokenUsage()
        self.updated_at = time.time()

    def turn_count(self) -> int:
        """统计完成的对话轮次(以 user 消息数为准)"""
        return sum(1 for m in self.messages if m.get("role") == "user")


# ============================================================
# 持久化: SQLite Store
# ============================================================

class SessionStore:
    """
    SQLite 实现的会话存储。

    单文件、零依赖、可携带。
    表结构故意简单 —— 一张 sessions 表存元信息, 一张 messages 表存消息。
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS sessions (
        id           TEXT PRIMARY KEY,
        title        TEXT NOT NULL,
        system_prompt TEXT NOT NULL,
        created_at   REAL NOT NULL,
        updated_at   REAL NOT NULL,
        usage_json   TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS messages (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id  TEXT NOT NULL,
        seq         INTEGER NOT NULL,
        role        TEXT NOT NULL,
        content_json TEXT NOT NULL,
        FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, seq);
    CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC);
    """

    def __init__(self, db_path: str | Path = "~/.my-agent/sessions.db"):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        # 每次新建连接 —— SQLite 在多线程下最稳的方式
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        with closing(self._connect()) as conn:
            conn.executescript(self.SCHEMA)
            conn.commit()

    # ----- 保存/加载 -----

    def save(self, session: Session) -> None:
        """全量保存一个 session(覆盖)"""
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO sessions(id, title, system_prompt, created_at, updated_at, usage_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    system_prompt = excluded.system_prompt,
                    updated_at = excluded.updated_at,
                    usage_json = excluded.usage_json
                """,
                (
                    session.id,
                    session.title,
                    session.system_prompt,
                    session.created_at,
                    session.updated_at,
                    json.dumps(session.usage.__dict__),
                ),
            )
            # 简单粗暴: 先删后插。会话不会太长, 性能够用。
            # 想优化的话可以做增量 append, 但要维护 seq 偏移。
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session.id,))
            conn.executemany(
                "INSERT INTO messages(session_id, seq, role, content_json) VALUES (?, ?, ?, ?)",
                [
                    (session.id, i, msg.get("role", ""), json.dumps(msg, ensure_ascii=False))
                    for i, msg in enumerate(session.messages)
                ],
            )
            conn.commit()

    def load(self, session_id: str) -> Session | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT id, title, system_prompt, created_at, updated_at, usage_json "
                "FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                return None

            id_, title, sp, created, updated, usage_json = row
            session = Session(
                id=id_,
                title=title,
                system_prompt=sp,
                created_at=created,
                updated_at=updated,
                usage=TokenUsage(**json.loads(usage_json)),
            )

            msg_rows = conn.execute(
                "SELECT content_json FROM messages WHERE session_id = ? ORDER BY seq",
                (session_id,),
            ).fetchall()
            session.messages = [json.loads(r[0]) for r in msg_rows]
            return session

    def delete(self, session_id: str) -> None:
        with closing(self._connect()) as conn:
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            conn.commit()

    def list_sessions(self, limit: int = 50) -> list[dict]:
        """列出最近的会话(元信息, 不含消息体)"""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT id, title, created_at, updated_at, "
                "(SELECT COUNT(*) FROM messages WHERE session_id = sessions.id) as msg_count "
                "FROM sessions ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "id": r[0],
                "title": r[1],
                "created_at": r[2],
                "updated_at": r[3],
                "message_count": r[4],
            }
            for r in rows
        ]
