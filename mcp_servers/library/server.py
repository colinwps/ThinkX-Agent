"""
图书馆 MCP Server (只读版)

按需求只暴露查询类工具:
- search_books     按关键词/分类查图书
- get_book         按 ISBN 看单本图书
- find_reader      按读者证号/手机号/姓名查读者
- get_reader_loans 看读者当前在借 + 借阅历史 + 未结罚款
- list_overdue     列出全馆超期未还的图书
- library_stats    图书馆整体统计

业务的写操作(借/还/付款)由独立流程负责, Agent 不直接执行写操作。
"""
import datetime
import os
import sys
import time
from pathlib import Path

# 让 server 可以被 python script 直接跑
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from mcp.server.fastmcp import FastMCP

from mcp_servers.library.db import LibraryDB


DB_PATH = os.environ.get("LIBRARY_DB_PATH", "/tmp/library.db")

db = LibraryDB(DB_PATH)
mcp = FastMCP("library")


def _fmt_time(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _mask_phone(phone: str) -> str:
    """脱敏: 138****8000"""
    if len(phone) >= 11:
        return phone[:3] + "****" + phone[-4:]
    return phone


# ============================================================
# 图书查询
# ============================================================

@mcp.tool()
def search_books(query: str = "", category: str = "") -> str:
    """
    按条件查询图书。
    参数:
        query: 关键词(书名、作者、ISBN 都可以)。空字符串表示不按关键词过滤。
        category: 分类过滤(如 '科幻'、'文学'、'技术'、'历史'、'推理')。空表示不过滤。
    返回: 图书列表(含 ISBN、书名、作者、库存信息)
    """
    books = db.search_books(query=query, category=category or None)
    if not books:
        return "没有找到符合条件的图书"
    lines = [f"找到 {len(books)} 本图书:"]
    for b in books:
        lines.append(
            f"  - [{b.isbn}] 《{b.title}》 {b.author} "
            f"({b.category}, {b.publish_year}) - 库存 {b.available}/{b.total}"
        )
    return "\n".join(lines)


@mcp.tool()
def get_book(isbn: str) -> str:
    """
    按 ISBN 查单本图书的详细信息。
    """
    b = db.get_book(isbn)
    if b is None:
        return f"找不到 ISBN 为 {isbn} 的图书"
    return (
        f"《{b.title}》\n"
        f"  作者: {b.author}\n"
        f"  ISBN: {b.isbn}\n"
        f"  分类: {b.category}, 出版年份: {b.publish_year}\n"
        f"  库存: {b.available}/{b.total}"
    )


# ============================================================
# 读者查询
# ============================================================

@mcp.tool()
def find_reader(card_no: str = "", phone: str = "", name: str = "") -> str:
    """
    查询读者基础信息。三种方式择一:
    - card_no: 读者证号(如 R001), 精确查
    - phone: 手机号, 精确查
    - name: 姓名(支持模糊匹配)
    返回: 读者基础信息列表(姓名/证号/脱敏后的手机号/信用分)
    """
    results = []
    if card_no:
        r = db.get_reader(card_no)
        if r:
            results = [r]
    elif phone:
        r = db.find_reader_by_phone(phone)
        if r:
            results = [r]
    elif name:
        results = db.find_reader_by_name(name)
    else:
        return "请提供 card_no、phone 或 name 中至少一个"

    if not results:
        return "没找到符合条件的读者"

    lines = [f"找到 {len(results)} 位读者:"]
    for r in results:
        lines.append(
            f"  - {r.name} ({r.card_no}) "
            f"手机 {_mask_phone(r.phone)} 信用分 {r.credit_score}"
        )
    return "\n".join(lines)


@mcp.tool()
def get_reader_loans(card_no: str) -> str:
    """
    查读者的借阅情况: 当前在借 + 借阅历史(最近 5 条) + 未结清罚款。
    """
    reader = db.get_reader(card_no)
    if reader is None:
        return f"找不到读者 {card_no}"

    lines = [f"读者: {reader.name} ({reader.card_no}) 信用分 {reader.credit_score}"]

    # 当前在借
    open_loans = db.open_loans(card_no)
    if open_loans:
        lines.append(f"\n当前在借 {len(open_loans)} 本:")
        now = time.time()
        for loan in open_loans:
            book = db.get_book(loan.isbn)
            title = book.title if book else loan.isbn
            days_left = (loan.due_at - now) / 86400
            status = (
                f"剩余 {days_left:.0f} 天"
                if days_left >= 0
                else f"⚠ 已超期 {-days_left:.0f} 天"
            )
            lines.append(
                f"  - 《{title}》 借于 {_fmt_time(loan.borrowed_at)}, "
                f"应还 {_fmt_time(loan.due_at)} ({status})"
            )
    else:
        lines.append("\n当前无在借图书")

    # 借阅历史 (排除当前在借的)
    history = [l for l in db.loan_history(card_no, limit=20) if l.returned_at][:5]
    if history:
        lines.append(f"\n最近 {len(history)} 条已完成借阅:")
        for loan in history:
            book = db.get_book(loan.isbn)
            title = book.title if book else loan.isbn
            lines.append(
                f"  - 《{title}》 {_fmt_time(loan.borrowed_at)} → "
                f"{_fmt_time(loan.returned_at)}"
            )

    # 未结清罚款
    fines = db.unpaid_fines(card_no)
    if fines:
        total = sum(f["amount"] for f in fines)
        lines.append(f"\n⚠ 未结清罚款 ¥{total:.2f} ({len(fines)} 笔):")
        for f in fines:
            lines.append(f"  - 单号 {f['id']}: ¥{f['amount']:.2f} ({f['reason']})")

    return "\n".join(lines)


# ============================================================
# 全局查询
# ============================================================

@mcp.tool()
def list_overdue_loans() -> str:
    """
    列出全馆所有超期未归还的图书。用于馆员日常催还。
    """
    loans = db.overdue_loans()
    if not loans:
        return "目前没有超期未还的图书 👍"
    now = time.time()
    lines = [f"全馆共 {len(loans)} 笔超期未还:"]
    for loan in loans:
        book = db.get_book(loan.isbn)
        reader = db.get_reader(loan.card_no)
        title = book.title if book else loan.isbn
        name = reader.name if reader else loan.card_no
        days_over = (now - loan.due_at) / 86400
        lines.append(
            f"  - 《{title}》 借给 {name} ({loan.card_no}), "
            f"应还 {_fmt_time(loan.due_at)} (超期 {days_over:.0f} 天)"
        )
    return "\n".join(lines)


@mcp.tool()
def library_stats() -> str:
    """
    图书馆整体统计:
    馆藏量、可借量、读者数、在借数、超期数、未结罚款总额、最热门图书 top5
    """
    s = db.stats()
    lines = [
        f"馆藏: {s['total_books']} 本 (可借 {s['available_books']})",
        f"读者: {s['total_readers']} 人",
        f"在借: {s['open_loans']} 本 (超期 {s['overdue_loans']} 本)",
        f"未结罚款总额: ¥{s['unpaid_fines_total']:.2f}",
        "",
        "最热门图书 (累计借阅次数):",
    ]
    for hot in s["hot_books"]:
        lines.append(f"  - 《{hot['title']}》 {hot['author']}: {hot['borrows']} 次")
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="stdio")
