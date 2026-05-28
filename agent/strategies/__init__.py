"""
策略子系统: Plan-and-Execute + Reflection

设计:
- plan.py:    "先规划后执行"模式 - 复杂任务先输出计划再分步执行
- reflect.py: "答完反思"模式 - 给完答案后自评质量, 必要时重答

两个都是"调几次额外 LLM 来换更好输出"的折衷, 不是所有场景都该用:
- Plan 适合: 多步、有依赖、需要分解的任务
- Reflect 适合: 输出质量敏感(代码、报告、关键决策)的场景

为什么不实现 Replan(检测计划失效后重新规划):
- 学习项目复杂度太高
- 实践中"plan 一次 + react 灵活执行"已经够用
- Replan 容易陷入死循环
"""
from .plan import Planner, PlanResult
from .reflect import Reflector, ReflectionResult

__all__ = [
    "Planner", "PlanResult",
    "Reflector", "ReflectionResult",
]
