"""
种子数据: 给图书馆库塞入测试数据
跑一次后, library.db 里就有书 + 读者 + 一些历史借阅记录
"""
import random
import time

from .db import LibraryDB


BOOKS = [
    # (isbn, title, author, total, category, year)
    ("9787536692930", "三体", "刘慈欣", 5, "科幻", 2008),
    ("9787536693968", "三体II 黑暗森林", "刘慈欣", 4, "科幻", 2008),
    ("9787536695832", "三体III 死神永生", "刘慈欣", 4, "科幻", 2010),
    ("9787020024759", "百年孤独", "马尔克斯", 3, "文学", 1967),
    ("9787544291170", "活着", "余华", 6, "文学", 1993),
    ("9787540237974", "白夜行", "东野圭吾", 3, "推理", 1999),
    ("9787544253994", "解忧杂货店", "东野圭吾", 5, "推理", 2012),
    ("9787100170581", "人类简史", "尤瓦尔·赫拉利", 4, "历史", 2011),
    ("9787508647357", "未来简史", "尤瓦尔·赫拉利", 3, "历史", 2016),
    ("9787121393365", "深入浅出 Python", "Paul Barry", 2, "技术", 2017),
    ("9787121375385", "Designing Data-Intensive Applications", "Martin Kleppmann", 2, "技术", 2017),
    ("9787115545381", "凤凰架构", "周志明", 3, "技术", 2021),
]


READERS = [
    # (card_no, name, phone, id_card, credit_score)
    ("R001", "张三", "13800138001", "110101199001011234", 100),
    ("R002", "李四", "13800138002", "110101199102022345", 95),
    ("R003", "王五", "13800138003", "110101199203033456", 100),
    ("R004", "赵六(信用差)", "13800138004", "110101199304044567", 55),  # 信用分低
    ("R005", "钱七", "13800138005", "110101199405055678", 80),
]


def seed(db_path: str = "/tmp/library.db", reset: bool = True):
    """生成种子数据。reset=True 时先清空再灌入。"""
    if reset:
        import os
        if os.path.exists(db_path):
            os.unlink(db_path)

    db = LibraryDB(db_path)

    # 插入图书
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect(db_path)) as conn:
        for isbn, title, author, total, category, year in BOOKS:
            conn.execute(
                "INSERT INTO books(isbn, title, author, total, available, category, publish_year) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (isbn, title, author, total, total, category, year),
            )
        # 插入读者
        now = time.time()
        for card_no, name, phone, id_card, credit in READERS:
            conn.execute(
                "INSERT INTO readers(card_no, name, phone, id_card, credit_score, registered_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (card_no, name, phone, id_card, credit, now - 86400 * 365),
            )
        conn.commit()

    # 造一些历史借阅: 大部分已还(便于看历史), 留 3 条未还(其中 1 条超期)
    random.seed(42)
    history_loans = [
        # (card_no, isbn, days_ago_borrowed, returned_days_later)
        ("R001", "9787544291170", 60, 28),   # 已还
        ("R001", "9787100170581", 90, 30),   # 已还
        ("R002", "9787540237974", 45, 35),   # 已还(超期 5 天 -> 罚款)
        ("R003", "9787536692930", 100, 25),  # 已还
        ("R005", "9787544253994", 70, 30),   # 已还
    ]
    open_loans = [
        ("R001", "9787536692930", 5, None),         # 借了 5 天
        ("R002", "9787121393365", 35, None),        # 借了 35 天 - 超期 5 天!
        ("R003", "9787508647357", 10, None),
    ]

    with closing(sqlite3.connect(db_path)) as conn:
        for card_no, isbn, ago, ret_after in history_loans:
            borrowed = now - ago * 86400
            due = borrowed + 30 * 86400
            returned = borrowed + ret_after * 86400 if ret_after else None
            conn.execute(
                "INSERT INTO loans(card_no, isbn, borrowed_at, due_at, returned_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (card_no, isbn, borrowed, due, returned),
            )
            # 超期还的, 加一条罚款
            if returned and returned > due:
                days_late = (returned - due) / 86400
                amount = round(min(days_late * 0.5, 20.0), 2)
                conn.execute(
                    "INSERT INTO fines(card_no, loan_id, amount, reason, paid, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (card_no, conn.execute("SELECT last_insert_rowid()").fetchone()[0],
                     amount, f"超期 {days_late:.1f} 天", 0, returned),
                )

        for card_no, isbn, ago, _ in open_loans:
            borrowed = now - ago * 86400
            due = borrowed + 30 * 86400
            conn.execute(
                "INSERT INTO loans(card_no, isbn, borrowed_at, due_at, returned_at) "
                "VALUES (?, ?, ?, ?, NULL)",
                (card_no, isbn, borrowed, due),
            )
            conn.execute(
                "UPDATE books SET available = available - 1 WHERE isbn = ?", (isbn,)
            )
        conn.commit()

    print(f"种子数据已生成: {db_path}")
    print(f"  - {len(BOOKS)} 本图书")
    print(f"  - {len(READERS)} 个读者")
    print(f"  - {len(history_loans) + len(open_loans)} 条借阅记录")


if __name__ == "__main__":
    seed()
