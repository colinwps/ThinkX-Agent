"""工具注册表: 统一管理所有工具"""
from __future__ import annotations

from typing import Iterable

from .base import Tool


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"工具 {tool.name} 已注册")
        self._tools[tool.name] = tool

    def register_many(self, tools: Iterable[Tool]) -> None:
        for t in tools:
            self.register(t)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def to_openai_schemas(self) -> list[dict]:
        """一次性输出所有工具的 schema, 直接传给 LLM"""
        return [t.to_openai_schema() for t in self._tools.values()]

    def execute(self, name: str, arguments: dict) -> str:
        """按名字调度执行"""
        tool = self.get(name)
        if tool is None:
            return f"[错误] 未知工具: {name}"
        return tool.execute(arguments)

    def __len__(self) -> int:
        return len(self._tools)

    def __repr__(self) -> str:
        return f"ToolRegistry({list(self._tools.keys())})"
