"""
历史压缩器(摘要式)

核心思路:
- 把"早期对话"压成一段简洁摘要
- 保留"最近 N 轮"原样不动 (用户在意的是最近的细节)
- 摘要塞回 messages 前面作为一条 system 消息

为什么不丢掉历史:
- 用户经常引用早期对话("还记得我说过 X 吗")
- 摘要至少保留语义关键点

为什么不动最近 N 轮:
- 最近的对话信息密度高, 压缩损失大
- 工具调用的 message 序列必须保持完整 (assistant tool_calls + tool result 必须一一对应)

prompt cache 配合策略:
- 压缩破坏 cache 前缀 (因为往中间塞了摘要)
- 所以压缩要"少做" - 阈值高一点
- 压缩后, 接下来一段对话会重新建 cache
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .token_estimator import estimate_messages_tokens


SUMMARIZE_PROMPT = """请把以下对话历史压缩成一段简洁的摘要, 用第三人称。
要求:
1. 保留关键事实: 用户提到的具体名字、数字、ID、文件路径等
2. 保留重要决定: 用户已确认/否决了什么
3. 保留进行中的任务上下文
4. 省略寒暄、客套、重复内容
5. 直接输出摘要正文, 不要"以下是摘要"之类的开场白

对话历史:
{history_text}
"""


@dataclass
class CompressionResult:
    summary: str
    messages_before: int
    messages_after: int
    tokens_before: int
    tokens_after: int
    saved_tokens: int

    @property
    def compression_ratio(self) -> float:
        if self.tokens_before == 0:
            return 0.0
        return 1.0 - self.tokens_after / self.tokens_before


class HistoryCompressor:
    """
    用 LLM 摘要的方式压缩 messages 历史。

    用法:
        compressor = HistoryCompressor(client=openai_client, model="deepseek-chat")
        new_messages, result = compressor.compress(messages, keep_recent=6)
    """

    def __init__(
        self,
        # 一个"调 LLM 的函数"。我们不强依赖 OpenAI client, 用回调更解耦
        # 签名: llm_call(prompt: str) -> str (返回摘要文本)
        llm_call: Callable[[str], str],
    ):
        self.llm_call = llm_call

    def compress(
        self,
        messages: list[dict],
        keep_recent: int = 6,
    ) -> tuple[list[dict], CompressionResult]:
        """
        压缩 messages, 返回 (新 messages, 压缩报告)

        keep_recent: 保留最后 N 条消息原样不动
                     (注意是 message 数, 不是 turn 数 -- 一个 turn 可能有多条 msg)
        """
        if len(messages) <= keep_recent:
            # 太短, 不用压缩
            return messages, CompressionResult(
                summary="", messages_before=len(messages),
                messages_after=len(messages),
                tokens_before=estimate_messages_tokens(messages),
                tokens_after=estimate_messages_tokens(messages),
                saved_tokens=0,
            )

        tokens_before = estimate_messages_tokens(messages)

        # ----- 分割: 早期(要压缩) vs 最近(保留) -----
        # 关键修正: keep_recent 不能落在"工具调用三元组"中间
        # tool_calls 的 assistant 后面必须紧跟 tool 消息, 否则模型会报错
        cut_point = self._safe_cut_point(messages, len(messages) - keep_recent)

        early = messages[:cut_point]
        recent = messages[cut_point:]

        if not early:
            return messages, CompressionResult(
                summary="", messages_before=len(messages),
                messages_after=len(messages),
                tokens_before=tokens_before, tokens_after=tokens_before,
                saved_tokens=0,
            )

        # ----- 渲染早期历史为文本 -----
        history_text = self._render_messages(early)

        # ----- 调 LLM 生成摘要 -----
        summary = self.llm_call(SUMMARIZE_PROMPT.format(history_text=history_text))
        summary = summary.strip()

        # ----- 把摘要拼成一条 system 消息, 放到 recent 前面 -----
        summary_msg = {
            "role": "system",
            "content": f"[历史摘要 - 之前的 {len(early)} 条消息已被压缩]\n{summary}",
        }
        new_messages = [summary_msg] + recent

        tokens_after = estimate_messages_tokens(new_messages)

        return new_messages, CompressionResult(
            summary=summary,
            messages_before=len(messages),
            messages_after=len(new_messages),
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            saved_tokens=tokens_before - tokens_after,
        )

    @staticmethod
    def _safe_cut_point(messages: list[dict], desired: int) -> int:
        """
        找一个安全的切割点 (>= desired)。

        切割点不能落在 "assistant(tool_calls) -> tool(result)" 中间, 否则
        切割后会出现 tool message 没有对应的 tool_calls, LLM 会报错。

        策略: 从 desired 开始往后找, 直到下一条 message 不是 tool。
        """
        n = len(messages)
        if desired >= n:
            return n
        if desired <= 0:
            return 0

        cut = desired
        # 如果切割点正好把 tool 消息留在了后半段(它的 assistant 在前半段), 往后挪
        while cut < n and messages[cut].get("role") == "tool":
            cut += 1
        return cut

    @staticmethod
    def _render_messages(messages: list[dict]) -> str:
        """把 messages 列表渲染成给摘要 LLM 看的文本"""
        lines = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content") or ""

            if role == "system":
                lines.append(f"[系统] {content}")
            elif role == "user":
                lines.append(f"[用户] {content}")
            elif role == "assistant":
                tool_calls = msg.get("tool_calls") or []
                if tool_calls:
                    if content:
                        lines.append(f"[助手] {content}")
                    for tc in tool_calls:
                        fn = tc.get("function", {})
                        lines.append(
                            f"[助手→工具] {fn.get('name')}({fn.get('arguments', '{}')})"
                        )
                else:
                    lines.append(f"[助手] {content}")
            elif role == "tool":
                # 工具结果可能很长, 截断
                content_trim = content if len(content) < 500 else content[:500] + "..."
                lines.append(f"[工具结果] {content_trim}")

        return "\n".join(lines)


def make_default_compressor(openai_client, model: str = "deepseek-chat") -> HistoryCompressor:
    """
    便捷工厂: 用一个 OpenAI 兼容客户端构造 compressor
    """
    def llm_call(prompt: str) -> str:
        resp = openai_client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            # 关键: 摘要任务不希望模型啰嗦, 也不需要工具
            temperature=0.3,
            stream=False,
        )
        return resp.choices[0].message.content or ""

    return HistoryCompressor(llm_call=llm_call)
