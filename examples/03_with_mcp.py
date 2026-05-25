"""
Step 3: 本地工具 + MCP 工具一起用
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from agent.core import Agent  # noqa: E402
from agent.session import Session  # noqa: E402
from agent.tools.builtin import BUILTIN_TOOLS  # noqa: E402
from agent.tools.mcp_client import MCPClient  # noqa: E402
from agent.tools.registry import ToolRegistry  # noqa: E402


# ============================================================
# 1. 启动 MCP Client —— 它会拉起 demo_server.py 子进程
# ============================================================
server_path = Path(__file__).parent.parent / "mcp_servers" / "demo_server.py"

mcp = MCPClient(
    command=sys.executable,  # 用当前 Python 解释器
    args=[str(server_path)],  # 跑我们的 demo server
    name="demo",
)
mcp.start()
print(f"[MCP] 连接成功, 工具列表: {[t['name'] for t in mcp.list_tools()]}\n")


# ============================================================
# 2. 组装 Registry —— 本地工具和 MCP 工具混在一起
# ============================================================
registry = ToolRegistry()
registry.register_many(BUILTIN_TOOLS)  # 5 个本地工具
registry.register_many(mcp.to_tools())  # 3 个 MCP 工具

print(f"已注册 {len(registry)} 个工具:")
for t in registry.all():
    print(f"  - {t.name}: {t.description[:40]}...")
print()


# ============================================================
# 3. 跑测试 —— Agent 不知道哪个是本地哪个是 MCP
# ============================================================
if __name__ == "__main__":
    if not os.getenv("DEEPSEEK_API_KEY"):
        print("错误: 请先在 .env 中设置 DEEPSEEK_API_KEY")
        sys.exit(1)

    agent = Agent(registry=registry)

    # 用例 1: 用 MCP 工具
    agent.run(Session(), "帮我掷 3 个 20 面骰")
    print("\n" + "=" * 70 + "\n")

    # 用例 2: 用知识库(MCP 工具)
    agent.run(Session(), "退款政策是怎样的?")
    print("\n" + "=" * 70 + "\n")

    # 用例 3: 本地工具 + MCP 工具混合
    agent.run(
        Session(),
        "查一下现在几点, 然后把当前时间写到 /tmp/now.txt 里, 最后读出来验证一下"
    )
