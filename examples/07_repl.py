"""
Step A4 + B: CLI REPL with 可观测性

启动后你会得到:
- 流式输出 + 多轮对话 + 自动持久化
- 工具调用确认
- 历史命令翻看
- 内置 /traces /trace /span /cost 看每一次执行的 trace

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
from agent.observability import RegexRedactor, TraceStore, Tracer  # noqa: E402
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

# 可观测性: 默认启用 + 启用脱敏(覆盖常见敏感模式)
trace_store = TraceStore("~/.my-agent/sessions.db")  # 和 session 同库
tracer = Tracer(
    store=trace_store,
    redactor=RegexRedactor.with_common_patterns(),
)

agent = Agent(
    registry=registry,
    skill_manager=skill_manager,
    approval_policy=ApprovalPolicy.default_safe(),
    tracer=tracer,
)

session_store = SessionStore("~/.my-agent/sessions.db")

repl = REPL(agent=agent, store=session_store, trace_store=trace_store)
repl.main_loop()
