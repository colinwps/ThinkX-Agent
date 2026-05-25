"""
Step 4: 用 Skill 引导 Agent 完成专业任务
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from openpyxl import Workbook  # noqa: E402

from agent.core import Agent  # noqa: E402
from agent.session import Session  # noqa: E402
from agent.skills.manager import SkillManager  # noqa: E402
from agent.tools.builtin import BUILTIN_TOOLS  # noqa: E402
from agent.tools.registry import ToolRegistry  # noqa: E402


# ============================================================
# 1. 准备一个测试用的 Excel 文件(多 sheet), 用来验证 excel skill
# ============================================================
def prepare_test_excel(path: str = "/tmp/test_input.xlsx"):
    wb = Workbook()
    wb.remove(wb.active)

    for sheet_name, data in [
        ("Jan", [("name", "amount"), ("Alice", 100), ("Bob", 200)]),
        ("Feb", [("name", "amount"), ("Alice", 150), ("Charlie", 80)]),
        ("Mar", [("name", "amount"), ("Bob", 220), ("Charlie", 90)]),
    ]:
        ws = wb.create_sheet(sheet_name)
        for row in data:
            ws.append(row)
    wb.save(path)
    print(f"[准备] 测试 Excel 已生成: {path}")


# ============================================================
# 2. 组装 Agent
# ============================================================
if __name__ == "__main__":
    if not os.getenv("DEEPSEEK_API_KEY"):
        print("错误: 请先在 .env 中设置 DEEPSEEK_API_KEY")
        sys.exit(1)

    prepare_test_excel()

    project_root = Path(__file__).parent.parent

    registry = ToolRegistry()
    registry.register_many(BUILTIN_TOOLS)

    skill_manager = SkillManager(project_root / "skills")
    print(
        f"[Skill] 加载了 {len(skill_manager.all())} 个 skill: "
        f"{[s.name for s in skill_manager.all()]}\n"
    )

    agent = Agent(
        registry=registry,
        skill_manager=skill_manager,
    )

    # 看看 skill catalog 长啥样(调试用)
    print("─" * 60)
    print("Skill Catalog (会注入 system prompt):")
    print(skill_manager.render_catalog())
    print("─" * 60)
    print()

    # ============================================================
    # 3. 测试用例 —— 注意观察模型有没有主动 load_skill
    # ============================================================

    # 用例 1: Excel 任务 —— 应该看到模型先 load_skill("excel") 再操作
    agent.run(
        Session(),
        "我在 /tmp/test_input.xlsx 有一个多 sheet 的 Excel, "
        "帮我把所有 sheet 合并成一个, 输出到 /tmp/output_merged.xlsx, 然后告诉我合并后总共多少行"
    )
    print("\n" + "=" * 70 + "\n")

    # 用例 2: Git commit 任务 —— 应该看到模型 load_skill("git-commit")
    agent.run(
        Session(),
        "帮我写一条 commit message, 改动是: 给用户表加了一个 last_login_at 字段, "
        "顺便迁移了历史数据, 并加了对应的索引"
    )
    print("\n" + "=" * 70 + "\n")

    # 用例 3: 不相关的任务 —— 模型应该不会加载任何 skill
    agent.run(Session(), "3 的 10 次方是多少?")
