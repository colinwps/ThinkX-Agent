"""
价格查询的小桥, 避免 context 包直接依赖 observability 包。
直接转发到 observability.pricing 模块。
"""
from ..observability.pricing import get_pricing, Pricing

__all__ = ["get_pricing", "Pricing"]
