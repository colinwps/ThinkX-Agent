"""
一个最小 MCP Server, 通过 stdio 通信
独立运行: python mcp_servers/demo_server.py
"""
import random
from datetime import datetime

from mcp.server.fastmcp import FastMCP

# FastMCP 是官方 SDK 提供的高级封装, 自动处理 JSON-RPC 协议细节
mcp = FastMCP("demo-server")


# ============================================================
# 注意: 这里的 @mcp.tool() 和我们自己写的 @tool 装饰器是两套东西
# 它是 MCP SDK 提供的, 作用是把函数注册成 MCP Server 的工具
# ============================================================


@mcp.tool()
def get_current_time(timezone: str = "Asia/Shanghai") -> str:
    """获取当前时间。可指定时区, 默认上海。"""
    # 简化处理, 实际可用 zoneinfo
    now = datetime.now()
    return f"当前时间 ({timezone}): {now.strftime('%Y-%m-%d %H:%M:%S')}"


@mcp.tool()
def roll_dice(sides: int = 6, count: int = 1) -> str:
    """
    掷骰子。可指定骰子面数(默认6)和数量(默认1)。
    适用于: 需要随机数、模拟掷骰、做选择等场景。
    """
    if sides < 2 or count < 1 or count > 100:
        return "参数不合理: sides>=2, 1<=count<=100"
    rolls = [random.randint(1, sides) for _ in range(count)]
    return f"掷了 {count} 个 {sides} 面骰: {rolls}, 总和 {sum(rolls)}"


@mcp.tool()
def search_kb(query: str) -> str:
    """
    在内部知识库中搜索。
    适用于: 查询公司内部信息、产品文档、FAQ 等。
    """
    # 假装是个知识库
    kb = {
        "退款": "退款政策: 7天内无理由退款, 商品需保持原包装。",
        "运费": "运费政策: 满99元包邮, 偏远地区除外。",
        "客服": "客服电话 400-xxx-xxxx, 工作日 9-18 点。",
    }
    for key, value in kb.items():
        if key in query:
            return value
    return f"知识库中没找到与 '{query}' 相关的内容"


if __name__ == "__main__":
    # stdio 模式启动 —— Client 会通过子进程的 stdin/stdout 与我们通信
    mcp.run(transport="stdio")
