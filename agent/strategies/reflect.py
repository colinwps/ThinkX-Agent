"""
Reflection 策略

工作流程:
1. Agent 给完最终答案 (Done event)
2. 用一个便宜模型评估: "这个回答好不好? 1-10 分 + 改进建议"
3. 如果分数 < 阈值, 让 Agent 看着建议重答一次

设计要点:
- 只做"整体反思", 不做工具结果反思(频次太高, 成本爆炸)
- 反思至多重答 1 次(防死循环)
- 评分用 JSON 拿结构化结果, 不依赖模型自由发挥

阈值建议:
- 7: 较严, 会触发不少重答, 适合质量敏感场景
- 5: 较松, 大部分不触发, 只兜底明显失败的回答
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable


REFLECT_PROMPT = """你是一个严格的回答审核员。请评估下面这个 AI 助手对用户问题的回答质量。

评估维度:
1. 完整性: 是否覆盖用户问的所有要点
2. 准确性: 是否有明显错误、自相矛盾、瞎编
3. 实用性: 用户能否基于此回答推进
4. 简洁性: 是否啰嗦、重复

# 用户问题
{user_input}

# 助手回答
{assistant_answer}

请输出 JSON, 包含两个字段:
- score: 1-10 整数 (1=很差, 10=完美)
- suggestions: 字符串, 给出具体的改进建议(50 字以内). 如果分数 >= 8, 可以为空字符串

例如: {{"score": 7, "suggestions": "缺少对边界情况的说明"}}

直接输出 JSON, 不要 markdown 代码块包裹, 不要任何额外说明。
"""


@dataclass
class ReflectionResult:
    score: int  # 1-10, 失败时 -1
    suggestions: str
    raw_response: str = ""
    parse_error: str | None = None

    @property
    def is_valid(self) -> bool:
        return self.parse_error is None and 1 <= self.score <= 10

    def needs_retry(self, threshold: int) -> bool:
        return self.is_valid and self.score < threshold


class Reflector:
    """用 LLM 评估回答质量"""

    def __init__(self, llm_call: Callable[[str], str]):
        self.llm_call = llm_call

    def evaluate(self, user_input: str, assistant_answer: str) -> ReflectionResult:
        if not assistant_answer.strip():
            return ReflectionResult(
                score=-1, suggestions="", parse_error="回答为空"
            )

        prompt = REFLECT_PROMPT.format(
            user_input=user_input,
            assistant_answer=assistant_answer,
        )
        try:
            raw = self.llm_call(prompt).strip()
        except Exception as e:
            return ReflectionResult(
                score=-1, suggestions="", parse_error=f"LLM 调用失败: {e}"
            )

        score, suggestions, err = _parse_reflection(raw)
        return ReflectionResult(
            score=score,
            suggestions=suggestions,
            raw_response=raw,
            parse_error=err,
        )


def _parse_reflection(text: str) -> tuple[int, str, str | None]:
    """解析模型返回的 JSON"""
    if not text:
        return -1, "", "空响应"

    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return -1, "", f"找不到 JSON 对象: {text[:100]}"

    json_text = cleaned[start:end + 1]
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as e:
        return -1, "", f"JSON parse 失败: {e}"

    if not isinstance(data, dict):
        return -1, "", "期望对象"

    try:
        score = int(data.get("score", -1))
    except (TypeError, ValueError):
        return -1, "", "score 不是整数"

    suggestions = str(data.get("suggestions", "")).strip()

    if not (1 <= score <= 10):
        return -1, suggestions, f"score 越界: {score}"

    return score, suggestions, None


def make_default_reflector(openai_client, model: str = "deepseek-chat") -> Reflector:
    """便捷工厂"""

    def llm_call(prompt: str) -> str:
        resp = openai_client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,  # 评分场景要稳定, 不要随机
            stream=False,
        )
        return resp.choices[0].message.content or ""

    return Reflector(llm_call=llm_call)
