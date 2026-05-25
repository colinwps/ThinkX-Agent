"""
Tool 抽象 + @tool 装饰器
核心思路: 用 Pydantic 把普通 Python 函数包装成 LLM 可调用的工具
"""
from __future__ import annotations

import inspect
from typing import Any, Callable, get_type_hints

from pydantic import Field, create_model


class Tool:
    """
    一个 Tool 封装了:
    - name: 工具名(LLM 用这个名字调用)
    - description: 工具说明(LLM 看这个决定要不要用)
    - parameters_schema: JSON Schema(告诉 LLM 参数怎么传)
    - func: 真正的 Python 函数
    """

    def __init__(
        self,
        func: Callable,
        name: str | None = None,
        description: str | None = None,
    ):
        self.func = func
        self.name = name or func.__name__
        # 如果没显式给 description, 就用 docstring 的第一段
        self.description = description or (inspect.getdoc(func) or "").strip()

        if not self.description:
            raise ValueError(
                f"工具 {self.name} 没有 description, 也没有 docstring。"
                f"LLM 不知道这个工具是干嘛的, 会乱调用。"
            )

        # 自动从函数签名生成 JSON Schema
        self.parameters_schema = self._build_schema()

    def _build_schema(self) -> dict:
        """
        用 Pydantic 从函数签名生成 JSON Schema
        关键技巧: create_model 动态创建一个 Pydantic Model, 然后用它生成 schema
        """
        sig = inspect.signature(self.func)
        type_hints = get_type_hints(self.func)

        fields = {}
        for param_name, param in sig.parameters.items():
            if param_name == "self":
                continue

            # 类型: 用 type hint, 没有就当 str
            annotation = type_hints.get(param_name, str)

            # 默认值: 有就用, 没有就标记为必填(...)
            if param.default is inspect.Parameter.empty:
                default = ...  # Pydantic 用 ... 表示必填
            else:
                default = param.default

            fields[param_name] = (annotation, Field(default=default))

        # 动态造一个 Pydantic Model
        model = create_model(f"{self.name}Args", **fields)

        # 生成 JSON Schema, 去掉 Pydantic 加的 title 字段(LLM 不需要)
        schema = model.model_json_schema()
        schema.pop("title", None)
        return schema

    def to_openai_schema(self) -> dict:
        """转成 OpenAI / DeepSeek function calling 要的格式"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema,
            },
        }

    def execute(self, arguments: dict[str, Any]) -> str:
        """
        执行工具。无论函数返回什么, 都转成字符串
        (因为要塞回 messages 给 LLM 看, LLM 只认文本)

        重点: 出错不抛异常, 而是返回错误信息字符串。
        这样 LLM 能看到错误并自我修正。
        """
        try:
            result = self.func(**arguments)
            return str(result)
        except Exception as e:
            return f"[工具 {self.name} 执行出错] {type(e).__name__}: {e}"

    def __repr__(self) -> str:
        return f"Tool(name={self.name!r})"


def tool(name: str | None = None, description: str | None = None):
    """
    装饰器: 把普通函数变成 Tool

    两种用法:
        @tool
        def my_func(...): ...

        @tool(name="custom_name", description="...")
        def my_func(...): ...
    """
    # 情况 1: @tool (不带括号), 此时 name 实际是被装饰的函数
    if callable(name):
        return Tool(func=name)

    # 情况 2: @tool(...) (带参数)
    def decorator(func: Callable) -> Tool:
        return Tool(func=func, name=name, description=description)

    return decorator
