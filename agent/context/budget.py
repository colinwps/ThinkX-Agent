"""
Token 预算管理

负责"调 LLM 之前"的一道关口:
- 估算当前 messages 大小
- 如果超过阈值, 触发压缩
- 给上层报告"压缩了没"以便写 trace

为什么阈值是百分比而非绝对值:
不同模型 context window 不一样(deepseek-chat 64K, qwen-max 32K, glm-4 128K),
按百分比设置更通用。

为什么阈值是 60% 而非 90%:
- token 估算本身有 ±15% 偏差
- 触发压缩 -> 压缩 LLM 调用本身也会生成新 token
- 给"压缩 + 新一轮对话 + 工具结果"留足空间
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .compressor import CompressionResult, HistoryCompressor
from .token_estimator import estimate_messages_tokens


# 模型 context window (token 总数)
MODEL_CONTEXT_WINDOWS = {
    "deepseek-chat": 64_000,
    "deepseek-reasoner": 64_000,
    "qwen-plus": 32_000,
    "qwen-max": 32_000,
    "qwen-turbo": 8_000,
    "glm-4-plus": 128_000,
    "glm-4-air": 128_000,
    "moonshot-v1-8k": 8_000,
    "moonshot-v1-32k": 32_000,
    "moonshot-v1-128k": 128_000,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
}


@dataclass
class BudgetCheck:
    """一次预算检查的结果"""
    current_tokens: int
    max_tokens: int
    threshold_tokens: int
    over_threshold: bool

    # 如果触发了压缩, 这里填实际压缩结果
    compressed: bool = False
    compression: Optional[CompressionResult] = None

    @property
    def usage_pct(self) -> float:
        return self.current_tokens / self.max_tokens if self.max_tokens else 0.0


class BudgetManager:
    """
    Token 预算管理。

    调 LLM 前调 check_and_compress(messages, system_prompt) ->
        - 返回新 messages (可能被压缩过) + BudgetCheck 报告
        - 调用方用新 messages 调 LLM

    默认行为:
    - 模型 context window 的 60% 时触发压缩
    - 压缩后保留最近 8 条消息(约 3-4 turn)原样
    """

    def __init__(
        self,
        compressor: Optional[HistoryCompressor] = None,
        max_tokens: int = 64_000,
        threshold_ratio: float = 0.60,
        keep_recent: int = 8,
    ):
        self.compressor = compressor
        self.max_tokens = max_tokens
        self.threshold_ratio = threshold_ratio
        self.keep_recent = keep_recent

    @property
    def threshold_tokens(self) -> int:
        return int(self.max_tokens * self.threshold_ratio)

    def estimate(self, messages: list[dict]) -> int:
        """估算 messages 大小"""
        return estimate_messages_tokens(messages)

    def check_and_compress(
        self,
        messages: list[dict],
    ) -> tuple[list[dict], BudgetCheck]:
        """
        检查预算 + 必要时压缩。

        返回 (可能被压缩过的 messages, 检查报告)
        如果没装 compressor, 即使超阈值也不压缩(只报告)。
        """
        current = self.estimate(messages)
        over = current > self.threshold_tokens

        check = BudgetCheck(
            current_tokens=current,
            max_tokens=self.max_tokens,
            threshold_tokens=self.threshold_tokens,
            over_threshold=over,
        )

        if not over or self.compressor is None:
            return messages, check

        # 触发压缩
        try:
            new_messages, comp_result = self.compressor.compress(
                messages, keep_recent=self.keep_recent
            )
            check.compressed = True
            check.compression = comp_result
            return new_messages, check
        except Exception as e:
            # 压缩失败不应阻断主流程, 退化为不压缩
            import sys
            print(f"[budget] 压缩失败, 回退到原消息: {e}", file=sys.stderr)
            return messages, check


def default_budget(model: str, compressor: Optional[HistoryCompressor] = None) -> BudgetManager:
    """根据模型自动选 context window 大小"""
    max_tokens = MODEL_CONTEXT_WINDOWS.get(model, 32_000)  # 未知模型保守 32K
    return BudgetManager(
        compressor=compressor,
        max_tokens=max_tokens,
        threshold_ratio=0.60,
        keep_recent=8,
    )
