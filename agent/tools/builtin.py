"""内置工具集合 —— 看看用 @tool 装饰器加工具有多简单"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .base import tool


@tool
def read_file(path: str) -> str:
    """读取指定路径的文本文件内容。用于查看文件里写了什么。"""
    p = Path(path).expanduser()
    if not p.exists():
        return f"文件不存在: {path}"
    if not p.is_file():
        return f"不是一个文件: {path}"
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"文件 {path} 不是 UTF-8 文本文件, 无法读取"


@tool
def write_file(path: str, content: str) -> str:
    """把内容写入指定路径的文件。会覆盖原有内容。"""
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"已写入 {len(content)} 个字符到 {path}"


@tool
def list_dir(path: str = ".") -> str:
    """列出指定目录下的文件和子目录。默认列当前目录。"""
    p = Path(path).expanduser()
    if not p.exists():
        return f"目录不存在: {path}"
    if not p.is_dir():
        return f"不是一个目录: {path}"

    items = []
    for item in sorted(p.iterdir()):
        marker = "/" if item.is_dir() else ""
        items.append(f"{item.name}{marker}")
    return "\n".join(items) if items else "(空目录)"


@tool
def run_shell(command: str) -> str:
    """
    在 shell 中执行命令, 返回 stdout 和 stderr。
    适用于: 查看系统信息、运行简单脚本、统计文件等。
    注意: 不要执行危险命令(rm -rf 之类)。
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = f"[exit code: {result.returncode}]\n"
        if result.stdout:
            output += f"[stdout]\n{result.stdout}\n"
        if result.stderr:
            output += f"[stderr]\n{result.stderr}"
        return output.strip()
    except subprocess.TimeoutExpired:
        return "[错误] 命令执行超时(>30s)"


@tool
def calculate(expression: str) -> str:
    """执行数学表达式计算, 支持加减乘除、括号、幂运算等。例如: (3+5)*2 或 2**10"""
    try:
        # 注意: 生产环境别用 eval, 这里只是演示
        result = eval(expression, {"__builtins__": {}}, {})
        return f"{expression} = {result}"
    except Exception as e:
        return f"计算错误: {e}"


# 方便外部一次性导入
BUILTIN_TOOLS = [read_file, write_file, list_dir, run_shell, calculate]
