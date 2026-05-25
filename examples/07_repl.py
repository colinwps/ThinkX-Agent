"""
Step A4: CLI REPL —— 主题 A 的最终形态

启动后你会得到一个像 ChatGPT 一样的命令行界面:
- 流式输出
- 多轮对话, 自动保存到 SQLite
- 危险工具调用前会问 y/n
- 历史命令 ↑↓ 翻
- 内置 /help /tokens /sessions /yolo 等斜杠命令

启动:  python -m examples.07_repl
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from agent import Agent, SessionStore  # noqa: E402
from agent.approval import ApprovalPolicy  # noqa: E402
from agent.cli import REPL  # noqa: E402
from agent.skills.manager import SkillManager  # noqa: E402
from agent.tools.builtin import BUILTIN_TOOLS  # noqa: E402
from agent.tools.registry import ToolRegistry  # noqa: E402


if not os.getenv("DEEPSEEK_API_KEY"):
    print("错误: 请在 .env 中设置 DEEPSEEK_API_KEY")
    sys.exit(1)


# ============================================================
# 组装
# ============================================================
project_root = Path(__file__).parent.parent

registry = ToolRegistry()
registry.register_many(BUILTIN_TOOLS)

skills_dir = project_root / "skills"
skill_manager = SkillManager(skills_dir) if skills_dir.exists() else None

agent = Agent(
    registry=registry,
    skill_manager=skill_manager,
    # 默认安全模式: run_shell / write_file 需要确认
    approval_policy=ApprovalPolicy.default_safe(),
)

store = SessionStore("~/.my-agent/sessions.db")

repl = REPL(agent=agent, store=store)
repl.main_loop()
