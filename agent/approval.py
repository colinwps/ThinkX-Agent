"""
工具调用审批机制

设计要点:
- ApprovalPolicy 是策略对象, 决定一个工具调用是 ALLOW / DENY / ASK
- 策略是组合的: 你可以叠加多个规则(白名单 + 黑名单 + 默认 ask)
- Agent 不关心怎么"问用户", 它只调用一个 approve(tool_name, args) 函数
- "怎么问"由调用方决定: CLI 弹 y/n, Web 推 WebSocket, 测试场景直接 yes/no
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class Decision(str, Enum):
    ALLOW = "allow"  # 直接放行
    DENY = "deny"    # 直接拒绝
    ASK = "ask"      # 问用户


@dataclass
class ApprovalPolicy:
    """
    工具审批策略

    判定优先级(从高到低):
    1. always_allow 名单 -> ALLOW
    2. always_deny  名单 -> DENY
    3. require_ask  名单 -> ASK
    4. default_decision

    用户在 CLI 里可以临时切换:
    - /yolo: 把 default_decision 改成 ALLOW(全自动)
    - /careful: 把所有工具加入 require_ask
    """
    default_decision: Decision = Decision.ALLOW
    always_allow: set[str] = field(default_factory=set)
    always_deny: set[str] = field(default_factory=set)
    require_ask: set[str] = field(default_factory=set)

    def decide(self, tool_name: str) -> Decision:
        if tool_name in self.always_allow:
            return Decision.ALLOW
        if tool_name in self.always_deny:
            return Decision.DENY
        if tool_name in self.require_ask:
            return Decision.ASK
        return self.default_decision

    @classmethod
    def default_safe(cls) -> "ApprovalPolicy":
        """默认的"安全模式": 危险工具都需要确认, 其他放行"""
        return cls(
            default_decision=Decision.ALLOW,
            require_ask={
                "run_shell",     # 任意 shell 命令, 最危险
                "write_file",    # 可能覆盖重要文件
            },
        )

    @classmethod
    def yolo(cls) -> "ApprovalPolicy":
        """全自动模式: 所有工具直接放行(自己负责后果)"""
        return cls(default_decision=Decision.ALLOW)

    @classmethod
    def paranoid(cls) -> "ApprovalPolicy":
        """偏执模式: 所有工具都问"""
        return cls(default_decision=Decision.ASK)


# 审批回调签名: 给定工具名和参数, 返回 (是否允许, 给模型的反馈消息)
# 反馈消息: 用户拒绝时可以让用户附一句"为什么拒绝", 给模型作参考
ApprovalCallback = Callable[[str, dict], tuple[bool, str]]


def auto_approve(tool_name: str, arguments: dict) -> tuple[bool, str]:
    """什么都不问, 直接放行 —— 用于非交互场景"""
    return True, ""


def auto_deny(tool_name: str, arguments: dict) -> tuple[bool, str]:
    """什么都不问, 直接拒绝 —— 用于测试"""
    return False, "(测试模式: 自动拒绝)"
