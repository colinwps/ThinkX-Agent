"""
MCP Client 适配器
核心目标: 把远程 MCP Server 的工具, 包装成本地 Tool 对象
这样 Agent 主循环完全不用区分"本地工具"和"MCP 工具"
"""
from __future__ import annotations

import asyncio
import atexit
import threading
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .base import Tool


class MCPClient:
    """
    管理与一个 MCP Server 的连接。

    设计取舍:
    MCP SDK 是异步的(asyncio), 但我们的 Agent 主循环是同步的。
    最干净的做法是把 Agent 也改成异步, 但那样改动太大。
    这里用一个常驻的后台事件循环线程, 把异步调用同步化。
    """

    def __init__(self, command: str, args: list[str], name: str = "mcp"):
        """
        command + args 指定怎么启动 MCP Server 子进程。
        例如: command="python", args=["mcp_servers/demo_server.py"]
        """
        self.name = name
        self.params = StdioServerParameters(command=command, args=args)

        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session: ClientSession | None = None
        self._exit_stack: AsyncExitStack | None = None
        self._started = False

    # ----- 后台事件循环管理 -----

    def _start_loop(self):
        """在后台线程里跑一个事件循环, 用来执行所有 async 操作"""
        self._loop = asyncio.new_event_loop()

        def run():
            asyncio.set_event_loop(self._loop)
            self._loop.run_forever()

        self._thread = threading.Thread(
            target=run, daemon=True, name=f"mcp-loop-{self.name}"
        )
        self._thread.start()

    def _run_async(self, coro):
        """从同步代码调用异步函数的桥梁"""
        assert self._loop is not None
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=60)

    # ----- 启动 / 关闭 -----

    def start(self) -> None:
        """启动 MCP Server 子进程, 建立连接, 初始化会话"""
        if self._started:
            return
        self._start_loop()
        self._run_async(self._async_start())
        self._started = True
        # 注册退出时清理, 避免子进程僵尸
        atexit.register(self.stop)

    async def _async_start(self):
        # AsyncExitStack 用来管理多个 async 上下文管理器的生命周期
        # 不用 async with 是因为我们要让 session 长期存活, 而不是用完就关
        self._exit_stack = AsyncExitStack()

        # 1. 启动 stdio 子进程, 拿到读写流
        read, write = await self._exit_stack.enter_async_context(
            stdio_client(self.params)
        )

        # 2. 建立 MCP 会话
        self._session = await self._exit_stack.enter_async_context(
            ClientSession(read, write)
        )

        # 3. initialize 握手(协议要求的第一步)
        await self._session.initialize()

    def stop(self) -> None:
        if not self._started:
            return
        try:
            self._run_async(self._async_stop())
        except Exception:
            pass
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._started = False

    async def _async_stop(self):
        if self._exit_stack:
            await self._exit_stack.aclose()

    # ----- 工具发现 + 调用 -----

    def list_tools(self) -> list[dict[str, Any]]:
        """从 Server 拉取工具列表"""
        result = self._run_async(self._session.list_tools())
        # result.tools 是一个 list, 每个元素有 name / description / inputSchema
        return [
            {
                "name": t.name,
                "description": t.description or "",
                "input_schema": t.inputSchema,
            }
            for t in result.tools
        ]

    def call_tool(self, name: str, arguments: dict) -> str:
        """调用一个 MCP 工具, 返回纯文本结果"""
        result = self._run_async(self._session.call_tool(name, arguments))

        # MCP 工具返回的是 content blocks 列表, 可能包含 text / image / 等
        # 学习阶段我们只处理 text, 把所有文本拼起来
        parts = []
        for block in result.content:
            if hasattr(block, "text"):
                parts.append(block.text)
            else:
                parts.append(f"[非文本内容: {type(block).__name__}]")

        text = "\n".join(parts)
        if result.isError:
            return f"[MCP 工具错误] {text}"
        return text

    # ----- 把所有 MCP 工具转成我们的 Tool 对象 -----

    def to_tools(self) -> list[Tool]:
        """
        关键魔法: 把 MCP 工具适配成 Tool 对象
        Tool 类原本是从 Python 函数生成 schema, 这里我们绕过这个机制,
        手动构造 Tool —— 因为 schema 已经从 Server 拿到了
        """
        tools = []
        for info in self.list_tools():
            tool = self._make_tool(info)
            tools.append(tool)
        return tools

    def _make_tool(self, info: dict) -> Tool:
        """从 MCP 工具描述构造一个 Tool 对象"""
        name = info["name"]
        description = info["description"]
        schema = info["input_schema"]

        # 关键技巧: Tool 类正常是用函数构造, 这里我们用 __new__ 绕过 __init__
        # 然后手动塞字段
        tool = Tool.__new__(Tool)
        tool.name = name
        tool.description = description or f"MCP 工具 {name}"
        tool.parameters_schema = schema

        # func 字段塞一个闭包, 调用时转发到 MCP Server
        def remote_call(**kwargs):
            return self.call_tool(name, kwargs)

        tool.func = remote_call
        return tool
