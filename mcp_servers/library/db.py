"""
图书馆数据库: schema + DAO (只读版)

按需求只保留查询能力, 不做借/还/罚款等写操作。

实体:
- books:    图书(书号、书名、作者、库存、总数)
- readers:  读者(读者证号、姓名、手机号、信用分)
- loans:    借阅记录(读者、图书、借出/到期/归还时间)
- fines:    罚款记录(读者、金额、原因、是否结清)

历史 loan/fine 数据用 seed 一次性灌入, 用来支持丰富的查询场景。
"""
from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    isbn        TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    author      TEXT NOT NULL,
    total       INTEGER NOT NULL,
    available   INTEGER NOT NULL,
    category    TEXT NOT NULL DEFAULT '其他',
    publish_year INTEGER
);

CREATE TABLE IF NOT EXISTS readers (
    card_no     TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    phone       TEXT NOT NULL,
    id_card     TEXT,
    credit_score INTEGER NOT NULL DEFAULT 100,
    registered_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS loans (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    card_no     TEXT NOT NULL,
    isbn        TEXT NOT NULL,
    borrowed_at REAL NOT NULL,
    due_at      REAL NOT NULL,
    returned_at REAL
);

CREATE INDEX IF NOT EXISTS idx_loans_card ON loans(card_no);

CREATE TABLE IF NOT EXISTS fines (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    card_no     TEXT NOT NULL,
    loan_id     INTEGER NOT NULL,
    amount      REAL NOT NULL,
    reason      TEXT NOT NULL,
    paid        INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL
);
"""


# ============================================================
# 数据类
# ============================================================

@dataclass
class Book:
    isbn: str
    title: str
    author: str
    total: int
    available: int
    category: str = "其他"
    publish_year: int | None = None


@dataclass
class Reader:
    card_no: str
    name: str
    phone: str
    id_card: str | None
    credit_score: int
    registered_at: float


@dataclass
class Loan:
    id: int
    card_no: str
    isbn: str
    borrowed_at: float
    due_at: float
    returned_at: float | None


# ============================================================
# 数据访问层 (只读)
# ============================================================

class LibraryDB:
    def __init__(self, db_path: str | Path = "/tmp/library.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ----- Books -----

    def search_books(
        self, query: str = "", category: str | None = None, limit: int = 20
    ) -> list[Book]:
        sql = "SELECT * FROM books WHERE 1=1"
        params: list = []
        if query:
            sql += " AND (title LIKE ? OR author LIKE ? OR isbn = ?)"
            params.extend([f"%{query}%", f"%{query}%", query])
        if category:
            sql += " AND category = ?"
            params.append(category)
        sql += " ORDER BY title LIMIT ?"
        params.append(limit)
        with closing(self._connect()) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [Book(**dict(r)) for r in rows]

    def get_book(self, isbn: str) -> Book | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM books WHERE isbn = ?", (isbn,)).fetchone()
        return Book(**dict(row)) if row else None

    # ----- Readers -----

    def get_reader(self, card_no: str) -> Reader | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM readers WHERE card_no = ?", (card_no,)
            ).fetchone()
        return Reader(**dict(row)) if row else None

    def find_reader_by_phone(self, phone: str) -> Reader | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM readers WHERE phone = ?", (phone,)
            ).fetchone()
        return Reader(**dict(row)) if row else None

    def find_reader_by_name(self, name: str, limit: int = 10) -> list[Reader]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM readers WHERE name LIKE ? LIMIT ?",
                (f"%{name}%", limit),
            ).fetchall()
        return [Reader(**dict(r)) for r in rows]

    # ----- Loans (只查询) -----

    def open_loans(self, card_no: str) -> list[Loan]:
        """未归还的借阅"""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM loans WHERE card_no = ? AND returned_at IS NULL "
                "ORDER BY due_at",
                (card_no,),
            ).fetchall()
        return [Loan(**dict(r)) for r in rows]

    def loan_history(self, card_no: str, limit: int = 10) -> list[Loan]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM loans WHERE card_no = ? ORDER BY borrowed_at DESC LIMIT ?",
                (card_no, limit),
            ).fetchall()
        return [Loan(**dict(r)) for r in rows]

    def overdue_loans(self) -> list[Loan]:
        """全馆所有超期未归还的借阅"""
        now = time.time()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM loans WHERE returned_at IS NULL AND due_at < ? "
                "ORDER BY due_at",
                (now,),
            ).fetchall()
        return [Loan(**dict(r)) for r in rows]

    # ----- Fines (只查询) -----

    def unpaid_fines(self, card_no: str) -> list[dict]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT id, amount, reason, created_at FROM fines "
                "WHERE card_no = ? AND paid = 0 ORDER BY created_at",
                (card_no,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ----- 统计 -----

    def stats(self) -> dict:
        with closing(self._connect()) as conn:
            stats = {}
            stats["total_books"] = conn.execute(
                "SELECT COALESCE(SUM(total), 0) FROM books"
            ).fetchone()[0]
            stats["available_books"] = conn.execute(
                "SELECT COALESCE(SUM(available), 0) FROM books"
            ).fetchone()[0]
            stats["total_readers"] = conn.execute(
                "SELECT COUNT(*) FROM readers"
            ).fetchone()[0]
            stats["open_loans"] = conn.execute(
                "SELECT COUNT(*) FROM loans WHERE returned_at IS NULL"
            ).fetchone()[0]
            stats["overdue_loans"] = conn.execute(
                "SELECT COUNT(*) FROM loans WHERE returned_at IS NULL AND due_at < ?",
                (time.time(),),
            ).fetchone()[0]
            stats["unpaid_fines_total"] = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM fines WHERE paid = 0"
            ).fetchone()[0]

            hot = conn.execute(
                "SELECT b.title, b.author, COUNT(l.id) AS borrows "
                "FROM books b LEFT JOIN loans l ON b.isbn = l.isbn "
                "GROUP BY b.isbn ORDER BY borrows DESC LIMIT 5"
            ).fetchall()
            stats["hot_books"] = [dict(r) for r in hot]
            return stats
