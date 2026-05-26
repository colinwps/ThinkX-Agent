"""
工具/函数级超时

实现思路:
线程级超时 —— 把函数扔到一个 thread 里跑, 主线程 join 等指定时长。
超时后函数返回错误, 但后台线程任由其跑完(不强杀)。

为什么不强杀:
- Python 没有干净的强杀线程的方法
- 强杀可能导致数据库连接泄漏、文件句柄不关、锁不释放等
- 学习项目我们接受"超时返回, 后台让它跑完"的折中

什么时候不能用:
- 真正卡死的同步 IO (网络无响应) -> 这个方案仍然可用, 但后台线程会一直占着
- 严格安全场景需要进程隔离 (subprocess + timeout)

run_shell 走的是 subprocess.run(timeout=...) 路线, 那个是真的能杀掉。
这里的 with_timeout 是给"其他工具"用的通用方案。
"""
from __future__ import annotations

import threading
from typing import Any, Callable, TypeVar

T = TypeVar("T")


class TimeoutError(Exception):
    """函数执行超时"""


def with_timeout(seconds: float, func: Callable[..., T], *args, **kwargs) -> T:
    """
    同步执行 func, 超过 seconds 抛 TimeoutError。

    限制:
    - func 内部如果不响应中断, 实际线程会继续跑(我们不强杀)
    - 不保证内部的 IO 立即停下

    返回:
    - func 的返回值; 超时则抛 TimeoutError; func 自己抛的异常透传出去
    """
    result_box: list[Any] = []
    error_box: list[BaseException] = []

    def target():
        try:
            result_box.append(func(*args, **kwargs))
        except BaseException as e:
            error_box.append(e)

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout=seconds)

    if t.is_alive():
        # 超时了, 但线程仍在跑 —— 任由它跑(daemon 进程退出时会被回收)
        raise TimeoutError(
            f"函数 {func.__name__} 执行超过 {seconds}s, 已放弃等待"
        )

    if error_box:
        raise error_box[0]
    return result_box[0]


def timeout_tool(tool, seconds: float):
    """
    给一个 Tool 包上超时控制。
    超时时返回错误字符串(不抛异常 - 模型能看到)。
    """
    from ..tools.base import Tool

    original_func = tool.func

    def wrapped(**kwargs):
        try:
            return with_timeout(seconds, original_func, **kwargs)
        except TimeoutError as e:
            return f"[超时] {e}"

    new_tool = Tool.__new__(Tool)
    new_tool.name = tool.name
    new_tool.description = tool.description
    new_tool.parameters_schema = tool.parameters_schema
    new_tool.func = wrapped
    return new_tool
