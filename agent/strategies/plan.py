"""
Plan-and-Execute 策略

工作流程:
1. 收到 user 任务后, 调一次 LLM(不带工具) -> 输出 plan (JSON 列表)
2. 把 plan 写入 system_prompt 附加段
3. 进入正常 ReAct 循环, 但 system 里加了"按照此计划执行"

为什么不让 plan 包含工具调用细节:
- 模型在没真跑工具前不知道结果, 详细 plan 反而误导
- 我们的 plan 只要"语义级步骤", 工具细节让 ReAct 阶段决定

为什么不做 Replan:
- 学习项目, 一次 plan 已经体现核心思想
- 真要做 Replan: 加一个 step_count, 每 N 步检查"是否还按计划在走"
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable


PLANNER_PROMPT = """你正在为一个 AI 助手规划如何完成用户的任务。

请把用户任务分解成 2-6 个具体步骤。要求:
1. 每步是一个清晰的、可执行的动作
2. 步骤之间有逻辑顺序(谁依赖谁)
3. 不要写具体的工具名(那是执行阶段决定的)
4. 不要做规划之外的事(寒暄、解释)

可用工具简介:
{tools_summary}

用户任务:
{user_task}

请输出 JSON 数组, 每项是一个步骤字符串。例如:
["先查询 X", "根据 X 的结果决定 Y", "最后输出 Z"]

直接输出 JSON, 不要 markdown 代码块包裹, 不要任何说明文字。
"""


@dataclass
class PlanResult:
    """一次规划的结果"""
    user_task: str
    steps: list[str] = field(default_factory=list)
    raw_response: str = ""
    parse_error: str | None = None

    @property
    def is_valid(self) -> bool:
        return self.parse_error is None and len(self.steps) > 0

    def render_for_system(self) -> str:
        """把 plan 渲染成可以塞进 system prompt 的文字"""
        if not self.steps:
            return ""
        lines = ["[执行计划 - 请按此顺序完成]"]
        for i, step in enumerate(self.steps, 1):
            lines.append(f"  {i}. {step}")
        lines.append("[当前没必要的话, 不要偏离这个计划]")
        return "\n".join(lines)


class Planner:
    """
    用 LLM 生成执行计划。
    """

    def __init__(self, llm_call: Callable[[str], str]):
        """
        llm_call: (prompt: str) -> str
                  调用 LLM 拿到 plan 文本。和 compressor 同款契约。
        """
        self.llm_call = llm_call

    def plan(self, user_task: str, tools_summary: str = "") -> PlanResult:
        """生成 plan。失败时 PlanResult.is_valid = False, 调用方应该跳过 plan 直接 react"""
        prompt = PLANNER_PROMPT.format(
            user_task=user_task,
            tools_summary=tools_summary or "(无工具描述)",
        )
        try:
            raw = self.llm_call(prompt).strip()
        except Exception as e:
            return PlanResult(user_task=user_task, parse_error=f"LLM 调用失败: {e}")

        steps, err = _parse_plan(raw)
        return PlanResult(
            user_task=user_task,
            steps=steps,
            raw_response=raw,
            parse_error=err,
        )


def _parse_plan(text: str) -> tuple[list[str], str | None]:
    """从 LLM 响应里提取 JSON 数组。容错: 去掉 markdown 围栏 / 前后说明文字"""
    if not text:
        return [], "空响应"

    # 1. 去掉 ```json ... ``` 围栏
    cleaned = text.strip()
    fence_match = re.search(r"```(?:json)?\s*(.+?)\s*```", cleaned, re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    # 2. 找第一个 [ 到最后一个 ] 之间的部分(去掉前后说明)
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return [], f"找不到 JSON 数组: {text[:100]}"

    json_text = cleaned[start:end + 1]
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as e:
        return [], f"JSON parse 失败: {e}"

    if not isinstance(data, list):
        return [], f"期望数组, 得到 {type(data).__name__}"

    steps = [str(x).strip() for x in data if str(x).strip()]
    if not steps:
        return [], "数组为空"
    return steps, None


def make_default_planner(openai_client, model: str = "deepseek-chat") -> Planner:
    """便捷工厂: 用 OpenAI 兼容客户端构造 Planner"""

    def llm_call(prompt: str) -> str:
        resp = openai_client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            stream=False,
        )
        return resp.choices[0].message.content or ""

    return Planner(llm_call=llm_call)
