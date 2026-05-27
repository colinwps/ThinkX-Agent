"""
Prompt Cache 命中观测

DeepSeek 默认开启 prompt caching:
- 完全自动, 无需配置
- usage.prompt_tokens_details.cached_tokens 字段返回命中数
- 缓存命中部分按 cached_input_per_1m 价格收费(便宜 4x)

我们要做的事:
- 计算节省的 token 数 + 钱
- 在 trace summary 里突出显示

实现要点:
- prompt 前缀稳定 = cache 容易命中
  -> 把"长期不变"的内容放前面 (system prompt + skill catalog)
  -> 把"每轮变化"的内容放后面 (user input + tool results)
- 压缩会破坏 cache (因为重写了中段历史)
  -> 所以压缩阈值要高, 不要轻易触发

我们的 messages 结构已经是这个布局:
    system  <- 稳定, 命中
    skill catalog  <- 稳定, 命中
    user_1 / assistant_1 / tool_1 ...  <- 累积, 越早的越容易命中
    user_N <- 当前的, 不命中
"""
from __future__ import annotations

from dataclasses import dataclass

from .pricing_helper import get_pricing


@dataclass
class CacheStats:
    """从一段时间内的 LLM 调用聚合的 cache 统计"""
    total_prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0

    @property
    def hit_rate(self) -> float:
        if self.total_prompt_tokens == 0:
            return 0.0
        return self.cached_tokens / self.total_prompt_tokens

    @property
    def uncached_tokens(self) -> int:
        return max(0, self.total_prompt_tokens - self.cached_tokens)

    def estimate_saving(self, model: str) -> float:
        """估算因为 cache 节省的钱(USD)"""
        pricing = get_pricing(model)
        if pricing is None or self.cached_tokens == 0:
            return 0.0
        # 节省 = 原本要付的全价 - 实际付的 cache 价
        full_price = self.cached_tokens * pricing.input_per_1m / 1_000_000
        cache_price = self.cached_tokens * pricing.cached_input_per_1m / 1_000_000
        return full_price - cache_price


def format_cache_savings(stats: CacheStats, model: str) -> str:
    """格式化 cache 节省的报告"""
    if stats.cached_tokens == 0:
        return "(无 cache 命中)"
    saving = stats.estimate_saving(model)
    pct = stats.hit_rate * 100
    return (
        f"命中 {stats.cached_tokens:,} / {stats.total_prompt_tokens:,} "
        f"prompt tokens ({pct:.0f}%), 节省约 ${saving:.4f}"
    )
