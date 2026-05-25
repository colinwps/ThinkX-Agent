"""
Step 1: 最小 Agent 实现 —— 单文件, 不依赖本项目的框架
目标: 跑通 ReAct 循环, 理解 Agent 的本质就是 "LLM + 工具调用 + 循环"
"""
import json
import os
import sys

from dotenv import load_dotenv
from openai import OpenAI
from rich import print as rprint
from rich.panel import Panel

load_dotenv()


# ============================================================
# 1. LLM 客户端: DeepSeek 兼容 OpenAI SDK, 只要换 base_url
# ============================================================
client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com/v1",
)
MODEL = "deepseek-chat"  # 也可用 deepseek-reasoner, 但 reasoner 暂不支持 function calling


# ============================================================
# 2. 工具实现: 先用最朴素的方式 —— 一个函数 + 一份 schema
#    后面 Step 2 我们再抽象成优雅的注册表
# ============================================================

def get_weather(city: str) -> str:
    """假装查天气, 先用 mock 数据走通流程"""
    fake_data = {
        "北京": "晴, 22°C, 西北风3级",
        "上海": "多云, 26°C, 东南风2级",
        "深圳": "雷阵雨, 29°C, 南风4级",
    }
    return fake_data.get(city, f"暂无 {city} 的天气数据")


def calculate(expression: str) -> str:
    """执行简单的数学计算"""
    try:
        result = eval(expression, {"__builtins__": {}}, {})
        return f"{expression} = {result}"
    except Exception as e:
        return f"计算错误: {e}"


# 工具 schema —— 这就是告诉模型 "你有哪些工具可用" 的关键
# 格式遵循 OpenAI function calling 规范, 国内模型都兼容
TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询指定城市的当前天气情况",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "城市名称, 例如: 北京、上海",
                    }
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": "执行数学表达式计算, 支持加减乘除和括号",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "数学表达式, 例如: (3+5)*2",
                    }
                },
                "required": ["expression"],
            },
        },
    },
]

# 工具名 -> 实际函数的映射, 执行时按名字找
TOOL_FUNCTIONS = {
    "get_weather": get_weather,
    "calculate": calculate,
}


# ============================================================
# 3. Agent 主循环 —— 整个 Agent 的核心就这一个函数
# ============================================================

def run_agent(user_input: str, max_iterations: int = 10) -> str:
    """
    Agent 主循环:
    1. 把用户输入塞进 messages
    2. 调 LLM
    3. 如果 LLM 想调工具 -> 执行工具 -> 结果塞回 messages -> 回到第 2 步
    4. 如果 LLM 直接给出回答 -> 结束
    """
    messages = [
        {
            "role": "system",
            "content": "你是一个有用的助手, 可以使用工具来回答用户问题。需要时主动调用工具, 不要瞎编。",
        },
        {"role": "user", "content": user_input},
    ]

    rprint(Panel(f"[bold cyan]用户输入[/]: {user_input}", border_style="cyan"))

    for iteration in range(max_iterations):
        rprint(f"\n[bold yellow]--- 第 {iteration + 1} 轮 ---[/]")

        # ----- 调用 LLM -----
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS_SCHEMA,
            tool_choice="auto",  # 让模型自己决定要不要调工具
        )
        msg = response.choices[0].message

        # ----- 把模型这一轮的输出加入历史 -----
        # 注意: 即使 content 是 None 也要加, 因为 tool_calls 信息在 msg 里
        messages.append(msg.model_dump(exclude_none=True))

        # ----- 情况 A: 模型决定不调工具, 直接回答 -----
        if not msg.tool_calls:
            rprint(Panel(f"[bold green]最终回答[/]:\n{msg.content}", border_style="green"))
            return msg.content or ""

        # ----- 情况 B: 模型要调一个或多个工具 -----
        rprint(f"[dim]模型决定调用 {len(msg.tool_calls)} 个工具[/]")

        for tool_call in msg.tool_calls:
            tool_name = tool_call.function.name
            tool_args = json.loads(tool_call.function.arguments)

            rprint(f"  [magenta]→ 调用[/] [bold]{tool_name}[/]({tool_args})")

            # ----- 真正执行工具 -----
            if tool_name in TOOL_FUNCTIONS:
                try:
                    result = TOOL_FUNCTIONS[tool_name](**tool_args)
                except Exception as e:
                    result = f"工具执行出错: {e}"
            else:
                result = f"未知工具: {tool_name}"

            rprint(f"  [magenta]← 结果[/]: {result}")

            # ----- 把工具结果塞回 messages, 让模型下一轮看到 -----
            # role="tool" + tool_call_id 是必须的, 这样模型知道这是哪次调用的结果
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": str(result),
            })

    rprint("[red]达到最大轮次, 强制结束[/]")
    return "抱歉, 处理超时了"


# ============================================================
# 4. 跑几个例子
# ============================================================

if __name__ == "__main__":
    if not os.getenv("DEEPSEEK_API_KEY"):
        print("错误: 请先在 .env 中设置 DEEPSEEK_API_KEY")
        sys.exit(1)

    # 例子 1: 单次工具调用
    run_agent("北京今天天气怎么样?")

    print("\n" + "=" * 70 + "\n")

    # 例子 2: 多次工具调用(模型需要先查天气, 再做判断)
    run_agent("帮我算一下 (123 + 456) * 7 等于多少")

    print("\n" + "=" * 70 + "\n")

    # 例子 3: 组合调用 —— 这个最能看出 Agent 的"智能"
    run_agent("如果北京的温度乘以 2 是多少度?")
