"""
Step 2: 体验抽象后的 Tool 系统 —— 看 Agent 用工具完成真实任务
"""
import os
import sys

from dotenv import load_dotenv

load_dotenv()

from agent.core import Agent  # noqa: E402
from agent.session import Session  # noqa: E402
from agent.tools.base import tool  # noqa: E402
from agent.tools.builtin import BUILTIN_TOOLS  # noqa: E402
from agent.tools.registry import ToolRegistry  # noqa: E402


# ============================================================
# 演示: 加自定义工具有多简单 —— 一个装饰器搞定
# ============================================================
@tool
def get_weather(city: str) -> str:
    """查询指定城市的当前天气情况。"""
    fake = {"北京": "晴 22°C", "上海": "多云 26°C", "深圳": "雷阵雨 29°C"}
    return fake.get(city, f"暂无 {city} 数据")


# ============================================================
# 组装 Agent
# ============================================================
registry = ToolRegistry()
registry.register_many(BUILTIN_TOOLS)
registry.register(get_weather)

print(f"已注册 {len(registry)} 个工具: {[t.name for t in registry.all()]}\n")

agent = Agent(registry=registry)


# ============================================================
# 测试用例 —— 从简单到复杂
# ============================================================
if __name__ == "__main__":
    if not os.getenv("DEEPSEEK_API_KEY"):
        print("错误: 请先在 .env 中设置 DEEPSEEK_API_KEY")
        sys.exit(1)

    # v2: 每个独立任务用一个新 Session
    s = Session()
    # 1. 单次工具调用
    agent.run(s, "北京今天天气怎么样?")
    print("\n" + "=" * 70 + "\n")

    # 2. 多步任务: 列出当前目录, 然后读其中一个文件
    s = Session()
    agent.run(s, "列出当前目录有哪些文件, 然后挑一个 .py 文件读一下, 告诉我它大概在干什么")
    print("\n" + "=" * 70 + "\n")

    # 3. 真·组合任务: 写文件 -> 读回来验证 -> 统计行数
    s = Session()
    agent.run(
        s,
        "在 /tmp/hello.txt 写入一首五言绝句(随便哪首都行), "
        "然后读回来确认, 再用 shell 命令统计这个文件有多少行多少字符"
    )
