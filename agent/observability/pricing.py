"""
模型价格表 + 成本估算

注意:
- 价格随时会变, 这里只是内置一些"截至本文撰写时的"已知值
- 单位统一: USD per million tokens (输入/输出/缓存命中)
- 用户可以 register_pricing() 覆盖默认值
- 国内模型用人民币定价的, 这里按"出版报价"换算成 USD 估算

更新策略:
官方价格页面随时会调整, 建议每月对一次。
DeepSeek:  https://api-docs.deepseek.com/quick_start/pricing
通义:      https://help.aliyun.com/zh/dashscope/developer-reference/tongyi-thousand-questions-metering-and-billing
智谱:      https://open.bigmodel.cn/pricing
Kimi:      https://platform.moonshot.cn/docs/pricing/chat
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Pricing:
    """单位: USD per 1M tokens"""
    input_per_1m: float = 0.0
    output_per_1m: float = 0.0
    cached_input_per_1m: float = 0.0  # 缓存命中价格(通常远低于普通输入)

    def calc(self, prompt_tokens: int, completion_tokens: int, cached_tokens: int = 0) -> float:
        """计算一次调用的成本(USD)"""
        # 普通输入 = 总 prompt - 缓存命中
        uncached_input = max(0, prompt_tokens - cached_tokens)
        cost = (
            uncached_input * self.input_per_1m / 1_000_000
            + cached_tokens * self.cached_input_per_1m / 1_000_000
            + completion_tokens * self.output_per_1m / 1_000_000
        )
        return cost


# ============================================================
# 内置价格表 (USD per 1M tokens)
# 这些价格仅供参考, 请以官方为准
# ============================================================

DEFAULT_PRICING: dict[str, Pricing] = {
    # ----- DeepSeek -----
    # https://api-docs.deepseek.com/quick_start/pricing
    "deepseek-chat": Pricing(input_per_1m=0.27, output_per_1m=1.10, cached_input_per_1m=0.07),
    "deepseek-reasoner": Pricing(input_per_1m=0.55, output_per_1m=2.19, cached_input_per_1m=0.14),

    # ----- 通义千问 (按出版报价 ¥7 RMB/USD 估算) -----
    "qwen-plus": Pricing(input_per_1m=0.40, output_per_1m=1.20),
    "qwen-max": Pricing(input_per_1m=1.40, output_per_1m=5.60),
    "qwen-turbo": Pricing(input_per_1m=0.05, output_per_1m=0.20),

    # ----- 智谱 -----
    "glm-4-plus": Pricing(input_per_1m=0.70, output_per_1m=0.70),
    "glm-4-air": Pricing(input_per_1m=0.07, output_per_1m=0.07),

    # ----- Kimi -----
    "moonshot-v1-8k": Pricing(input_per_1m=1.70, output_per_1m=1.70),
    "moonshot-v1-32k": Pricing(input_per_1m=3.40, output_per_1m=3.40),

    # ----- OpenAI(参考) -----
    "gpt-4o": Pricing(input_per_1m=2.50, output_per_1m=10.00, cached_input_per_1m=1.25),
    "gpt-4o-mini": Pricing(input_per_1m=0.15, output_per_1m=0.60, cached_input_per_1m=0.075),
}


# 运行时注册的价格 (用户覆盖 / 新模型)
_custom_pricing: dict[str, Pricing] = {}


def register_pricing(model: str, pricing: Pricing) -> None:
    """注册或覆盖一个模型的价格"""
    _custom_pricing[model] = pricing


def get_pricing(model: str) -> Pricing | None:
    """查模型价格, 找不到返回 None"""
    if model in _custom_pricing:
        return _custom_pricing[model]
    return DEFAULT_PRICING.get(model)


def estimate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int = 0,
) -> float:
    """估算一次调用的成本 (USD)。未知模型返回 0。"""
    pricing = get_pricing(model)
    if pricing is None:
        return 0.0
    return pricing.calc(prompt_tokens, completion_tokens, cached_tokens)


def format_cost(cost_usd: float) -> str:
    """漂亮地显示成本"""
    if cost_usd == 0:
        return "$0"
    if cost_usd < 0.0001:
        return f"${cost_usd:.6f}"
    if cost_usd < 0.01:
        return f"${cost_usd:.4f}"
    return f"${cost_usd:.4f}"
