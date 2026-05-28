"""
并行工具调度

模型返回 N 个 tool_calls 时, 默认串行执行;
打开 parallel=True 后, 用 ThreadPoolExecutor 并行。

关键设计:
1. 需要审批的工具必须串行(同时弹 N 个 modal 体验灾难)
2. 工具之间可能有隐式依赖(write_file 后 read_file)
   - 但我们信任模型: 模型在一次 tool_calls 里返回多个时, 它判断"没依赖"
   - 真有依赖的话, 模型会分两轮发(react 自然行为)
3. trace span 仍是 trace_run 的"平等子节点"(不互相嵌套)
4. 顺序保留: 即使并行执行, 结果按原 tool_calls 顺序 yield

为什么这样切分:
- 安全工具 -> 并行 (查询类一般无副作用)
- 审批工具 -> 串行 (用户体验 + 一次只决策一件事)
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable


@dataclass
class ToolExecutionResult:
    """单个工具执行的结果"""
    call_id: str
    name: str
    arguments: dict
    result: str
    approved: bool
    error: str | None = None  # 执行过程中的异常(rare, 通常工具内部已捕获)


def execute_tools_parallel(
    tool_calls: list,        # 每项是 (call_id, name, arguments) 三元组
    executor_fn: Callable[[str, dict], tuple[str, bool]],
    needs_approval: Callable[[str], bool],
    max_workers: int = 4,
) -> list[ToolExecutionResult]:
    """
    分组执行: 审批工具串行, 其余并行。
    返回结果按原 tool_calls 顺序排列。

    executor_fn: 一个 (name, args) -> (result_str, approved_bool) 的函数
                 内部该处理审批、执行、错误兜底
    needs_approval: 一个 name -> bool 的函数, 判断这个工具需不需要审批
    """
    if not tool_calls:
        return []

    # 把每个 call 标上原始 index, 保证最终顺序
    indexed = list(enumerate(tool_calls))

    needs_ask = [(i, tc) for i, tc in indexed if needs_approval(tc[1])]
    safe = [(i, tc) for i, tc in indexed if not needs_approval(tc[1])]

    results: dict[int, ToolExecutionResult] = {}

    # ----- 安全工具: 并行 -----
    if safe:
        # 单个直接调, 多个用 thread pool
        if len(safe) == 1:
            i, (cid, name, args) = safe[0]
            results[i] = _safe_call(cid, name, args, executor_fn)
        else:
            with ThreadPoolExecutor(max_workers=min(max_workers, len(safe))) as ex:
                futures = {
                    ex.submit(_safe_call, cid, name, args, executor_fn): i
                    for i, (cid, name, args) in safe
                }
                for fut in futures:
                    i = futures[fut]
                    results[i] = fut.result()

    # ----- 审批工具: 串行 -----
    for i, (cid, name, args) in needs_ask:
        results[i] = _safe_call(cid, name, args, executor_fn)

    # 按原顺序输出
    return [results[i] for i in sorted(results.keys())]


def _safe_call(call_id: str, name: str, args: dict, executor_fn) -> ToolExecutionResult:
    """单次工具执行, 异常兜底"""
    try:
        result, approved = executor_fn(name, args)
        return ToolExecutionResult(
            call_id=call_id, name=name, arguments=args,
            result=result, approved=approved,
        )
    except Exception as e:
        return ToolExecutionResult(
            call_id=call_id, name=name, arguments=args,
            result=f"[执行异常] {type(e).__name__}: {e}",
            approved=False, error=str(e),
        )
