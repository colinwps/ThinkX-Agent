"""
Trace 存储: SQLite

设计:
- 复用 sessions.db (默认), 多一组 trace_* 表
- traces 表存元信息 + 聚合统计 (便于列表/统计查询)
- spans 表存详细 span (按 trace_id 索引)
- 大字段 (payload) 单独列, 查询 trace 列表时不读它们
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from .models import Span, SpanKind, SpanStatus, Trace, TraceStatus


class TraceStore:
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS traces (
        id              TEXT PRIMARY KEY,
        session_id      TEXT,
        user_input      TEXT NOT NULL,
        final_output    TEXT NOT NULL,
        status          TEXT NOT NULL,
        started_at      REAL NOT NULL,
        ended_at        REAL,
        model           TEXT NOT NULL,
        iteration_count INTEGER NOT NULL DEFAULT 0,
        llm_call_count  INTEGER NOT NULL DEFAULT 0,
        tool_call_count INTEGER NOT NULL DEFAULT 0,
        total_prompt_tokens     INTEGER NOT NULL DEFAULT 0,
        total_completion_tokens INTEGER NOT NULL DEFAULT 0,
        total_cached_tokens     INTEGER NOT NULL DEFAULT 0,
        total_cost_usd  REAL NOT NULL DEFAULT 0,
        tags_json       TEXT NOT NULL DEFAULT '{}'
    );

    CREATE TABLE IF NOT EXISTS spans (
        id          TEXT PRIMARY KEY,
        trace_id    TEXT NOT NULL,
        parent_id   TEXT,
        kind        TEXT NOT NULL,
        name        TEXT NOT NULL,
        status      TEXT NOT NULL,
        started_at  REAL NOT NULL,
        ended_at    REAL,
        attributes_json TEXT NOT NULL DEFAULT '{}',
        payload_json    TEXT NOT NULL DEFAULT '{}',
        error       TEXT,
        FOREIGN KEY (trace_id) REFERENCES traces(id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_spans_trace ON spans(trace_id, started_at);
    CREATE INDEX IF NOT EXISTS idx_spans_kind ON spans(kind);
    CREATE INDEX IF NOT EXISTS idx_traces_started ON traces(started_at DESC);
    CREATE INDEX IF NOT EXISTS idx_traces_session ON traces(session_id);
    """

    def __init__(self, db_path: str | Path = "~/.my-agent/sessions.db"):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        with closing(self._connect()) as conn:
            conn.executescript(self.SCHEMA)
            conn.commit()

    # ============================================================
    # 写入
    # ============================================================

    def save_trace(self, trace: Trace) -> None:
        """全量保存(upsert) trace 元信息"""
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO traces(
                    id, session_id, user_input, final_output, status,
                    started_at, ended_at, model,
                    iteration_count, llm_call_count, tool_call_count,
                    total_prompt_tokens, total_completion_tokens, total_cached_tokens,
                    total_cost_usd, tags_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    final_output = excluded.final_output,
                    status = excluded.status,
                    ended_at = excluded.ended_at,
                    iteration_count = excluded.iteration_count,
                    llm_call_count = excluded.llm_call_count,
                    tool_call_count = excluded.tool_call_count,
                    total_prompt_tokens = excluded.total_prompt_tokens,
                    total_completion_tokens = excluded.total_completion_tokens,
                    total_cached_tokens = excluded.total_cached_tokens,
                    total_cost_usd = excluded.total_cost_usd
                """,
                (
                    trace.id, trace.session_id, trace.user_input, trace.final_output,
                    trace.status.value, trace.started_at, trace.ended_at, trace.model,
                    trace.iteration_count, trace.llm_call_count, trace.tool_call_count,
                    trace.total_prompt_tokens, trace.total_completion_tokens,
                    trace.total_cached_tokens, trace.total_cost_usd,
                    json.dumps(trace.tags, ensure_ascii=False),
                ),
            )
            conn.commit()

    def save_span(self, span: Span) -> None:
        """全量保存(upsert)一个 span"""
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO spans(
                    id, trace_id, parent_id, kind, name, status,
                    started_at, ended_at, attributes_json, payload_json, error
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    ended_at = excluded.ended_at,
                    attributes_json = excluded.attributes_json,
                    payload_json = excluded.payload_json,
                    error = excluded.error
                """,
                (
                    span.id, span.trace_id, span.parent_id,
                    span.kind.value, span.name, span.status.value,
                    span.started_at, span.ended_at,
                    json.dumps(span.attributes, ensure_ascii=False, default=str),
                    json.dumps(span.payload, ensure_ascii=False, default=str),
                    span.error,
                ),
            )
            conn.commit()

    # ============================================================
    # 查询
    # ============================================================

    def get_trace(self, trace_id: str) -> Trace | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM traces WHERE id = ?", (trace_id,)
            ).fetchone()
            if row is None:
                return None
            return self._row_to_trace(row, [d[0] for d in conn.execute(
                "SELECT * FROM traces LIMIT 0").description])

    def get_spans(self, trace_id: str) -> list[Span]:
        with closing(self._connect()) as conn:
            cur = conn.execute(
                "SELECT * FROM spans WHERE trace_id = ? ORDER BY started_at, id",
                (trace_id,),
            )
            cols = [d[0] for d in cur.description]
            return [self._row_to_span(row, cols) for row in cur.fetchall()]

    def list_traces(
        self,
        limit: int = 30,
        session_id: str | None = None,
        status: TraceStatus | None = None,
    ) -> list[Trace]:
        sql = "SELECT * FROM traces WHERE 1=1"
        params: list = []
        if session_id:
            sql += " AND session_id = ?"
            params.append(session_id)
        if status:
            sql += " AND status = ?"
            params.append(status.value)
        sql += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)

        with closing(self._connect()) as conn:
            cur = conn.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [self._row_to_trace(row, cols) for row in cur.fetchall()]

    def delete_trace(self, trace_id: str) -> None:
        with closing(self._connect()) as conn:
            conn.execute("DELETE FROM traces WHERE id = ?", (trace_id,))
            conn.commit()

    # ============================================================
    # 聚合统计
    # ============================================================

    def aggregate_cost(
        self, since: float | None = None, until: float | None = None
    ) -> dict:
        """统计指定时间范围内的总成本/token/调用次数"""
        sql = """
            SELECT
                COUNT(*) AS trace_count,
                COALESCE(SUM(llm_call_count), 0) AS llm_calls,
                COALESCE(SUM(tool_call_count), 0) AS tool_calls,
                COALESCE(SUM(total_prompt_tokens), 0) AS prompt_tokens,
                COALESCE(SUM(total_completion_tokens), 0) AS completion_tokens,
                COALESCE(SUM(total_cached_tokens), 0) AS cached_tokens,
                COALESCE(SUM(total_cost_usd), 0) AS cost_usd
            FROM traces WHERE 1=1
        """
        params: list = []
        if since is not None:
            sql += " AND started_at >= ?"
            params.append(since)
        if until is not None:
            sql += " AND started_at < ?"
            params.append(until)

        with closing(self._connect()) as conn:
            cur = conn.execute(sql, params)
            cols = [d[0] for d in cur.description]
            row = cur.fetchone()
        return dict(zip(cols, row))

    def aggregate_by_model(self, limit: int = 10) -> list[dict]:
        sql = """
            SELECT model,
                COUNT(*) AS traces,
                SUM(llm_call_count) AS calls,
                SUM(total_prompt_tokens) AS prompt_tokens,
                SUM(total_completion_tokens) AS completion_tokens,
                SUM(total_cost_usd) AS cost_usd
            FROM traces
            WHERE model != ''
            GROUP BY model
            ORDER BY cost_usd DESC
            LIMIT ?
        """
        with closing(self._connect()) as conn:
            cur = conn.execute(sql, (limit,))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    # ============================================================
    # 内部: row -> dataclass
    # ============================================================

    @staticmethod
    def _row_to_trace(row: tuple, cols: list[str]) -> Trace:
        d = dict(zip(cols, row))
        return Trace(
            id=d["id"],
            session_id=d["session_id"],
            user_input=d["user_input"],
            final_output=d["final_output"],
            status=TraceStatus(d["status"]),
            started_at=d["started_at"],
            ended_at=d["ended_at"],
            model=d["model"],
            iteration_count=d["iteration_count"],
            llm_call_count=d["llm_call_count"],
            tool_call_count=d["tool_call_count"],
            total_prompt_tokens=d["total_prompt_tokens"],
            total_completion_tokens=d["total_completion_tokens"],
            total_cached_tokens=d["total_cached_tokens"],
            total_cost_usd=d["total_cost_usd"],
            tags=json.loads(d["tags_json"] or "{}"),
        )

    @staticmethod
    def _row_to_span(row: tuple, cols: list[str]) -> Span:
        d = dict(zip(cols, row))
        return Span(
            id=d["id"],
            trace_id=d["trace_id"],
            parent_id=d["parent_id"],
            kind=SpanKind(d["kind"]),
            name=d["name"],
            status=SpanStatus(d["status"]),
            started_at=d["started_at"],
            ended_at=d["ended_at"],
            attributes=json.loads(d["attributes_json"] or "{}"),
            payload=json.loads(d["payload_json"] or "{}"),
            error=d["error"],
        )
