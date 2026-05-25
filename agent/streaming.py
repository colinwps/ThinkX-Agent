"""
流式响应解析

为什么需要这个模块:
OpenAI/DeepSeek 兼容 SDK 的流式响应里, 一条 assistant 消息可能被切成 N 个 chunk:
- 文本逐字到达
- tool_calls 也是分片来的, 每片可能只带某个工具的部分参数 JSON

我们要做的:
1. 累加 chunks 还原出完整的 assistant 消息(content + tool_calls)
2. 同时支持"边累加边回调"(让 CLI 能流式打印文字)
3. 流结束时, 输出完整的 message dict 给 Agent 主循环处理

这一段是流式 Agent 最容易写错的地方, 慢慢看。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterator


@dataclass
class _PartialToolCall:
    """累加中的 tool_call(还未完成)"""
    id: str = ""
    type: str = "function"
    name: str = ""
    arguments: str = ""  # JSON 字符串, 流式增量拼接


@dataclass
class StreamedMessage:
    """流式累加的最终产物"""
    content: str = ""
    tool_calls: list[_PartialToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    usage: object | None = None  # 模型返回的 usage 信息(最后一个 chunk)

    def to_message_dict(self) -> dict:
        """转成 OpenAI messages 里的 assistant 消息格式"""
        msg: dict = {"role": "assistant"}
        if self.content:
            msg["content"] = self.content
        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": tc.type,
                    "function": {"name": tc.name, "arguments": tc.arguments},
                }
                for tc in self.tool_calls
            ]
        return msg


# 文字回调签名: 每收到一个文字片段就调用一次(用于流式打印)
TextChunkCallback = Callable[[str], None]


def consume_stream(
    stream: Iterator,
    on_text: TextChunkCallback | None = None,
) -> StreamedMessage:
    """
    消费一个 OpenAI 风格的流, 返回累加完成的 message

    on_text: 每收到一段文本就触发(用于 REPL 流式打印)
    """
    result = StreamedMessage()
    # tool_calls 按 index 累加(每个 tool_call 有自己的 index)
    tc_buffer: dict[int, _PartialToolCall] = {}

    for chunk in stream:
        if not chunk.choices:
            # 有些 chunk(比如最后一个携带 usage 的)没有 choices
            if hasattr(chunk, "usage") and chunk.usage is not None:
                result.usage = chunk.usage
            continue

        choice = chunk.choices[0]
        delta = choice.delta

        # ----- 累加文本 -----
        if delta.content:
            result.content += delta.content
            if on_text is not None:
                on_text(delta.content)

        # ----- 累加 tool_calls -----
        if delta.tool_calls:
            for tc_delta in delta.tool_calls:
                idx = tc_delta.index
                if idx not in tc_buffer:
                    tc_buffer[idx] = _PartialToolCall()
                partial = tc_buffer[idx]

                # id 通常只在第一片出现
                if tc_delta.id:
                    partial.id = tc_delta.id
                if tc_delta.type:
                    partial.type = tc_delta.type

                # function.name / arguments 也是增量的
                if tc_delta.function:
                    if tc_delta.function.name:
                        partial.name += tc_delta.function.name
                    if tc_delta.function.arguments:
                        partial.arguments += tc_delta.function.arguments

        # 记录结束原因
        if choice.finish_reason:
            result.finish_reason = choice.finish_reason

        # usage 可能放在最后一个 chunk
        if hasattr(chunk, "usage") and chunk.usage is not None:
            result.usage = chunk.usage

    # 按 index 顺序输出
    result.tool_calls = [tc_buffer[i] for i in sorted(tc_buffer.keys())]
    return result
