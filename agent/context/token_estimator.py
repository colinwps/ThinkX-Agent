"""
Token 估算

目标:
- 在调 LLM 之前估算 messages 的 token 数, 用来做预算判断
- 不依赖远程 API (零延迟、零成本)

实现:
- 优先用 tiktoken (OpenAI 的 BPE 分词器, 装不上时退化为字符近似)
- DeepSeek/通义/智谱 都没公开自家分词器, 但它们用的都是 BPE 类
  实测 tiktoken cl100k_base 对中文估算偏差 ±15%, 做预算够用
- 中英文混合的近似规则: 中文字符 ~1.5 token, 英文 char ~0.25 token

校准说明:
精确字符数无所谓, 我们只是要"什么时候触发压缩"的近似信号。
偏差 ±15% 完全可接受 - 阈值设为窗口的 60% 而不是 95%, 留够余量。
"""
from __future__ import annotations

import json
import re
from functools import lru_cache

# tiktoken 是可选依赖
try:
    import tiktoken
    _HAS_TIKTOKEN = True
except ImportError:
    _HAS_TIKTOKEN = False


@lru_cache(maxsize=1)
def _get_encoder():
    """懒加载 tiktoken 编码器"""
    if not _HAS_TIKTOKEN:
        return None
    try:
        # cl100k_base 是 GPT-3.5/4 用的, 对 DeepSeek 等中文模型也凑合
        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None


_CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")


def estimate_tokens(text: str) -> int:
    """估算一段文本的 token 数"""
    if not text:
        return 0

    encoder = _get_encoder()
    if encoder is not None:
        try:
            return len(encoder.encode(text))
        except Exception:
            pass  # 编码失败, 退化到字符近似

    # 退化方案: 中文字符 * 1.5 + 其他字符 * 0.25
    chinese = len(_CHINESE_RE.findall(text))
    others = len(text) - chinese
    return int(chinese * 1.5 + others * 0.25)


def estimate_messages_tokens(messages: list[dict]) -> int:
    """
    估算 OpenAI 格式 messages 的总 token 数。

    包括: role 标记 + content + tool_calls 的 JSON 序列化体积。
    每条消息加 ~4 token 作为"协议开销" (role、分隔符等)。
    """
    total = 0
    for msg in messages:
        # 协议开销
        total += 4

        # content (可能是 str 或 None)
        content = msg.get("content")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            # 多模态 content
            for block in content:
                if isinstance(block, dict):
                    total += estimate_tokens(json.dumps(block, ensure_ascii=False))

        # tool_calls (assistant 调工具时)
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            for tc in tool_calls:
                # function name + arguments JSON
                fn = tc.get("function", {})
                total += estimate_tokens(fn.get("name", ""))
                total += estimate_tokens(fn.get("arguments", ""))
                total += 10  # tool_call 结构本身的开销

        # tool_call_id (tool 角色消息)
        if msg.get("tool_call_id"):
            total += 5

    return total
