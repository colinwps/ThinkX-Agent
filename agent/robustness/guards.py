"""
工具护栏

ToolResultGuard:
  把工具的输出截断到指定长度。截断时给模型一个明确的提示, 不是哑截断。

CommandValidator:
  shell 命令黑名单。在 run_shell 执行前过一遍, 命中黑名单直接拒绝。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from ..tools.base import Tool


# ============================================================
# 结果长度护栏
# ============================================================

@dataclass
class ToolResultGuard:
    """
    工具结果护栏: 截断超长输出。

    设计要点:
    - 截断时保留"头 + 尾"两端, 中间用省略号(LLM 看头尾上下文都重要)
    - 截断时给一个明确的标记, 让模型知道"被截断了, 可以更精确地调用"
    - 默认 8000 字符 (~ 2000 token), 大多数场景够用
    """
    max_chars: int = 8000
    keep_head_ratio: float = 0.7  # 截断时保留头部的比例

    def apply(self, result: str) -> str:
        if len(result) <= self.max_chars:
            return result

        truncated_bytes = len(result) - self.max_chars + 100  # 留余量给提示文字
        keep_head = int((self.max_chars - 100) * self.keep_head_ratio)
        keep_tail = self.max_chars - 100 - keep_head

        head = result[:keep_head]
        tail = result[-keep_tail:] if keep_tail > 0 else ""

        marker = (
            f"\n\n[...输出过长, 中间省略了 {truncated_bytes} 个字符。"
            f"如需查看完整内容, 请用更精确的参数(例如范围、过滤、grep)再调用。...]\n\n"
        )

        return head + marker + tail


def guard_tool(tool: Tool, guard: ToolResultGuard) -> Tool:
    """
    给一个 Tool 装上结果护栏。

    实现: 创建一个新 Tool 对象, func 包了一层调用。
    不改原 Tool 是为了避免共享同一 func 引用时互相影响。
    """
    original_func = tool.func

    def wrapped(**kwargs):
        result = original_func(**kwargs)
        return guard.apply(str(result))

    # 用 __new__ 绕过 __init__(因为 __init__ 会重新生成 schema)
    new_tool = Tool.__new__(Tool)
    new_tool.name = tool.name
    new_tool.description = tool.description
    new_tool.parameters_schema = tool.parameters_schema
    new_tool.func = wrapped
    return new_tool


# ============================================================
# 命令黑名单
# ============================================================

class DangerousCommandError(Exception):
    """命令命中黑名单"""


@dataclass
class CommandValidator:
    """
    Shell 命令黑名单。

    用法:
        validator = CommandValidator.default()
        validator.validate("rm -rf /")  # 抛 DangerousCommandError

    规则按顺序匹配, 第一个命中即拒绝。
    每条规则是 (正则模式, 拒绝原因)。

    说明:
    - 这不是真正的"安全沙箱", 只防误伤(模型一时糊涂)
    - 真正生产场景需要进程隔离 / Docker / SELinux
    """
    rules: list[tuple[str, str]] = field(default_factory=list)
    _compiled: list[tuple[re.Pattern, str]] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self):
        self._compiled = [(re.compile(p, re.IGNORECASE), r) for p, r in self.rules]

    @classmethod
    def default(cls) -> "CommandValidator":
        """一组保守的、明显恶意才匹配的规则"""
        return cls(rules=[
            # rm -rf 危险目录 (用零宽断言收尾, 而不是 \b, 因为目标含非词字符如 / ~)
            (r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\s+(/(\s|$|\*)|~(\s|$)|\$HOME|\.(\s|$)|/usr|/etc|/var|/home|/bin|/sbin)",
             "rm -rf 系统关键目录"),
            (r"\brm\s+(-[rf]+\s+)?--no-preserve-root\b",
             "rm 禁用了根目录保护"),

            # 磁盘格式化 / 写入
            (r"\bmkfs\.", "磁盘格式化命令"),
            (r"\bdd\s+.*\s+of=/dev/(sd|hd|nvme|disk)", "dd 写入物理磁盘"),

            # 系统配置文件改动(写入/截断)
            (r">\s*/etc/(passwd|shadow|sudoers|hosts)\b", "写入系统配置文件"),

            # fork 炸弹
            (r":\(\)\s*\{.*\|.*&.*\}.*;.*:", "fork 炸弹"),

            # 远程下载执行
            (r"\b(curl|wget)\s+[^\s]+\s*\|\s*(sudo\s+)?(bash|sh|zsh)\b",
             "下载后直接执行(curl | bash 模式)"),

            # 隐藏目标进程的 kill
            (r"\bkill(\s+-9)?\s+1\b", "kill init 进程"),
        ])

    def validate(self, command: str) -> None:
        """命中黑名单 -> 抛 DangerousCommandError"""
        for pattern, reason in self._compiled:
            if pattern.search(command):
                raise DangerousCommandError(
                    f"命令被黑名单拦截: {reason}. 完整命令: {command}"
                )

    def is_safe(self, command: str) -> bool:
        try:
            self.validate(command)
            return True
        except DangerousCommandError:
            return False


def make_safe_shell_tool(
    original_run_shell: Tool,
    validator: CommandValidator,
) -> Tool:
    """
    给 run_shell 包一层命令校验。
    用法: registry.register(make_safe_shell_tool(builtin.run_shell, CommandValidator.default()))
    """
    original_func = original_run_shell.func

    def safe_run(command: str) -> str:
        try:
            validator.validate(command)
        except DangerousCommandError as e:
            return f"[拒绝执行] {e}"
        return original_func(command=command)

    new_tool = Tool.__new__(Tool)
    new_tool.name = original_run_shell.name
    new_tool.description = original_run_shell.description
    new_tool.parameters_schema = original_run_shell.parameters_schema
    new_tool.func = safe_run
    return new_tool
