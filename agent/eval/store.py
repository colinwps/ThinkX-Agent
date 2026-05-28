"""
Eval Store: 把每次 eval run 存进 SQLite, 便于历史对比

表结构:
- eval_runs:  一次 run 的元信息(suite, config, 总分)
- eval_cases: 每个 case 的结果(passed, assertions JSON)
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

from .runner import CaseResult, RunResult


SCHEMA = """
CREATE TABLE IF NOT EXISTS eval_runs (
    run_id      TEXT PRIMARY KEY,
    suite_name  TEXT NOT NULL,
    started_at  REAL NOT NULL,
    finished_at REAL NOT NULL,
    pass_count  INTEGER NOT NULL,
    total_count INTEGER NOT NULL,
    total_cost_usd REAL NOT NULL,
    config_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS eval_cases (
    run_id      TEXT NOT NULL,
    case_id     TEXT NOT NULL,
    passed      INTEGER NOT NULL,
    duration_s  REAL NOT NULL,
    iterations  INTEGER NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    cost_usd    REAL NOT NULL,
    final_output TEXT NOT NULL,
    error       TEXT,
    assertions_json TEXT NOT NULL,
    tool_calls_json TEXT NOT NULL,
    PRIMARY KEY (run_id, case_id),
    FOREIGN KEY (run_id) REFERENCES eval_runs(run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_runs_suite ON eval_runs(suite_name, started_at);
"""


class EvalStore:
    def __init__(self, db_path: str | Path = "~/.my-agent/evals.db"):
        self.db_path = Path(str(db_path)).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def save_run(self, run: RunResult) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO eval_runs
                   (run_id, suite_name, started_at, finished_at,
                    pass_count, total_count, total_cost_usd, config_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (run.run_id, run.suite_name, run.started_at, run.finished_at,
                 run.pass_count, run.total_count, run.total_cost_usd,
                 json.dumps(run.config, ensure_ascii=False)),
            )
            for cr in run.cases:
                conn.execute(
                    """INSERT OR REPLACE INTO eval_cases
                       (run_id, case_id, passed, duration_s, iterations,
                        prompt_tokens, completion_tokens, cost_usd,
                        final_output, error, assertions_json, tool_calls_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (run.run_id, cr.case_id, int(cr.passed),
                     cr.duration_seconds, cr.iterations,
                     cr.prompt_tokens, cr.completion_tokens, cr.cost_usd,
                     cr.final_output, cr.error,
                     json.dumps([asdict(a) for a in cr.assertions], ensure_ascii=False),
                     json.dumps(cr.tool_calls, ensure_ascii=False)),
                )
            conn.commit()

    def list_runs(self, suite_name: str | None = None, limit: int = 20) -> list[dict]:
        with closing(self._connect()) as conn:
            if suite_name:
                rows = conn.execute(
                    """SELECT * FROM eval_runs WHERE suite_name = ?
                       ORDER BY started_at DESC LIMIT ?""",
                    (suite_name, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM eval_runs ORDER BY started_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    def get_run(self, run_id: str) -> dict | None:
        with closing(self._connect()) as conn:
            r = conn.execute(
                "SELECT * FROM eval_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if r is None:
                return None
            run = dict(r)
            cases = conn.execute(
                "SELECT * FROM eval_cases WHERE run_id = ?", (run_id,)
            ).fetchall()
            run["cases"] = [dict(c) for c in cases]
        return run

    def compare_runs(self, run_a: str, run_b: str) -> dict:
        """对比两个 run, 给出 diff"""
        a = self.get_run(run_a)
        b = self.get_run(run_b)
        if a is None or b is None:
            raise ValueError("找不到指定 run")

        cases_a = {c["case_id"]: c for c in a["cases"]}
        cases_b = {c["case_id"]: c for c in b["cases"]}

        all_ids = sorted(set(cases_a) | set(cases_b))
        diffs = []
        for cid in all_ids:
            ca, cb = cases_a.get(cid), cases_b.get(cid)
            if ca is None:
                diffs.append({"case_id": cid, "status": "only_in_b"})
                continue
            if cb is None:
                diffs.append({"case_id": cid, "status": "only_in_a"})
                continue
            if ca["passed"] != cb["passed"]:
                status = "regressed" if ca["passed"] and not cb["passed"] else "improved"
                diffs.append({"case_id": cid, "status": status,
                              "a_passed": bool(ca["passed"]),
                              "b_passed": bool(cb["passed"])})
            elif ca["cost_usd"] != cb["cost_usd"]:
                # 都通过但成本变了
                diffs.append({
                    "case_id": cid, "status": "same",
                    "cost_delta": cb["cost_usd"] - ca["cost_usd"],
                })

        return {"a": a, "b": b, "diffs": diffs}
