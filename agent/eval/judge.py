"""
LLM-as-Judge

工作流:
1. EvalRunner 拿一个 OpenAI 兼容 client (可以和 Agent 共用)
2. 包装成 judge_fn(criteria, ctx) -> (score, reason)
3. 跑 case 时, 把 LLMJudge.evaluate 替换为调用 judge_fn

为什么单独一个文件:
- 隔离 LLM 调用代码, assertions.py 保持纯数据/纯函数
- 便于测试: 假 judge_fn 直接 mock 一个函数即可
"""
from __future__ import annotations

import json
import re
from typing import Callable

from .assertions import AssertionResult, LLMJudge, RunContext


JUDGE_PROMPT = """你是一个严格的评分员。请评估 AI 助手的回答是否符合给定标准。

# 评判标准
{criteria}

# 用户输入
{user_input}

# 助手回答
{assistant_output}

# 助手调用的工具
{tools_summary}

请输出 JSON, 包含两个字段:
- score: 0-10 整数 (0=完全不符合, 10=完美符合)
- reason: 字符串, 简述评分理由 (50 字以内)

例如: {{"score": 8, "reason": "覆盖了主要信息但缺少边界说明"}}

直接输出 JSON, 不要 markdown 代码块, 不要任何额外说明。
"""


def make_judge_fn(openai_client, model: str = "deepseek-chat") -> Callable[[str, RunContext], tuple[float, str]]:
    """
    创建一个 judge 函数。

    Returns: (criteria, ctx) -> (score, reason)
    """
    def judge_fn(criteria: str, ctx: RunContext) -> tuple[float, str]:
        tools_summary = "无" if not ctx.tool_calls else "\n".join(
            f"- {tc['name']}({tc['arguments']})" for tc in ctx.tool_calls
        )

        prompt = JUDGE_PROMPT.format(
            criteria=criteria,
            user_input=ctx.user_input,
            assistant_output=ctx.final_output,
            tools_summary=tools_summary,
        )

        try:
            resp = openai_client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,  # 评分要稳定
                stream=False,
            )
            raw = (resp.choices[0].message.content or "").strip()
        except Exception as e:
            return 0.0, f"LLM 调用失败: {e}"

        return _parse_judge_response(raw)

    return judge_fn


def _parse_judge_response(text: str) -> tuple[float, str]:
    """容错解析 judge 响应"""
    if not text:
        return 0.0, "空响应"

    cleaned = text.strip()
    # 剥 markdown
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    # 找 JSON
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        return 0.0, f"找不到 JSON: {text[:50]}"

    try:
        data = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError as e:
        return 0.0, f"JSON 解析失败: {e}"

    try:
        score = float(data.get("score", 0))
    except (TypeError, ValueError):
        return 0.0, "score 不是数字"

    score = max(0.0, min(10.0, score))
    reason = str(data.get("reason", ""))
    return score, reason


def evaluate_llm_judge(
    judge: LLMJudge,
    ctx: RunContext,
    judge_fn: Callable[[str, RunContext], tuple[float, str]],
) -> AssertionResult:
    """跑一次 LLM judge 断言"""
    score, reason = judge_fn(judge.criteria, ctx)
    passed = score >= judge.threshold
    msg = f"score={score:.1f}/10 (阈值 {judge.threshold}): {reason}"
    return AssertionResult(passed=passed, message=msg, score=score)
