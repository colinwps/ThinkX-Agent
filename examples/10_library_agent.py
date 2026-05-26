"""
业务接入示例: 智能图书馆查询助手 (只读版)

启动后, 你可以在 REPL 里对话:
- "查一下《三体》还有库存吗"
- "13800138001 这个手机号的读者是谁"
- "读者 R001 借过哪些书? 现在手上还有什么?"
- "全馆现在有哪些超期没还的"
- "这个月最热门的书是什么"

只提供查询能力, 不支持借/还/付款等写操作。

综合了之前所有主题:
  v1: Tool/MCP/Skill
  v2: 会话化 + 流式 + REPL + 审批
  v3: Trace 完整观察
  v4: 工具结果护栏 + 命令黑名单 + LLM 重试 + MCP 重试
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
from agent.robustness import (  # noqa: E402
    CommandValidator,
    ToolResultGuard,
    guard_tool,
)
from agent.robustness.guards import make_safe_shell_tool
from agent.robustness.retry import default_mcp_retry_policy  # noqa: E402
from agent.tools.builtin import (  # noqa: E402
    calculate,
    list_dir,
    read_file,
    run_shell,
)
from agent.tools.mcp_client import MCPClient  # noqa: E402
from agent.tools.registry import ToolRegistry  # noqa: E402


if not os.getenv("DEEPSEEK_API_KEY"):
    print("错误: 请在 .env 中设置 DEEPSEEK_API_KEY")
    sys.exit(1)


# ============================================================
# 1. 准备图书馆数据库(每次启动重置, 便于演示)
# ============================================================
print("[准备] 重置图书馆数据库...")
from mcp_servers.library.seed import seed

LIBRARY_DB = "/tmp/library.db"
seed(LIBRARY_DB, reset=True)


# ============================================================
# 2. 启动图书馆 MCP Server (带重试 - 用户偏好"MCP 也重试")
# ============================================================
project_root = Path(__file__).parent.parent
server_path = project_root / "mcp_servers" / "library" / "server.py"

os.environ["LIBRARY_DB_PATH"] = LIBRARY_DB
library_mcp = MCPClient(
    command=sys.executable,
    args=[str(server_path)],
    name="library",
    retry_policy=default_mcp_retry_policy(),  # 关键: MCP 调用也重试
    env=os.environ.copy(),  # 关键: 把 LIBRARY_DB_PATH 等环境传给子进程
)
library_mcp.start()
print(
    f"[准备] 图书馆 MCP Server 已启动, 工具: "
    f"{[t['name'] for t in library_mcp.list_tools()]}"
)


# ============================================================
# 3. 组装工具集(应用稳健性护栏)
# ============================================================
registry = ToolRegistry()

# 本地工具: 结果护栏(默认 8000 字符)
guard = ToolResultGuard(max_chars=8000)
registry.register(guard_tool(read_file, guard))
registry.register(guard_tool(list_dir, guard))
registry.register(calculate)
safe_shell = make_safe_shell_tool(run_shell, CommandValidator.default())
registry.register(guard_tool(safe_shell, guard))

# MCP 工具: 业务工具也加结果护栏
for mcp_tool in library_mcp.to_tools():
    registry.register(guard_tool(mcp_tool, guard))


# ============================================================
# 4. 组装 Agent (含 trace + LLM 重试)
# ============================================================
trace_store = TraceStore("~/.my-agent/sessions.db")
tracer = Tracer(
    store=trace_store,
    redactor=RegexRedactor.with_common_patterns(),
)

# 只读场景: 只需要对系统危险操作(run_shell/write_file)做确认
# 业务工具全是查询, 默认放行就行
approval = ApprovalPolicy.default_safe()

agent = Agent(
    registry=registry,
    approval_policy=approval,
    tracer=tracer,
    # retry_policy 默认: LLM 调用自动 3 次重试 + 指数退避
)


# ============================================================
# 5. 自定义 system prompt
# ============================================================
LIBRARY_SYSTEM_PROMPT = """你是一个图书馆查询助手, 帮助馆员和读者查询图书馆相关信息。

你具备以下查询能力:
- 按关键词/分类/ISBN 查图书
- 按读者证号/手机号/姓名查读者
- 看读者的当前在借、借阅历史、未结清罚款
- 列出全馆超期未归还的图书 (催还场景)
- 图书馆整体统计

工作风格:
- 优先用工具查询, 不要凭印象作答
- 涉及读者隐私时, 复述手机号要用脱敏形式(MCP 工具返回的本身就是脱敏的, 不要去还原)
- 一次回答只需要必要信息, 不要把工具返回的内容原样照搬
- 用清晰简洁的中文回答, 必要时用 markdown 列表
- 你只能查询, 不能借/还/付款。如果用户提这些操作, 告诉他们去前台办理。
"""


# ============================================================
# 6. 启动 REPL
# ============================================================
session_store = SessionStore("~/.my-agent/sessions.db")
repl = REPL(agent=agent, store=session_store, trace_store=trace_store)

if repl.session is None:
    repl._new_session()
repl.session.system_prompt = LIBRARY_SYSTEM_PROMPT
session_store.save(repl.session)


print()
print("─" * 60)
print("🏛️  欢迎使用智能图书馆查询助手")
print("─" * 60)
print("可以尝试:")
print('  "查一下三体这本书"')
print('  "技术类有什么书"')
print('  "13800138001 是谁? 现在借了什么"')
print('  "全馆有哪些书超期没还"')
print('  "图书馆最热门的书是哪本"')
print()
print("REPL 命令: /help /tools /traces /trace <id> /cost /quit")
print("─" * 60)

repl.main_loop()
