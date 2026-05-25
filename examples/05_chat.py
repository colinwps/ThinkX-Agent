"""
Step A1: 多轮对话 + 会话持久化

验证:
- Session 维护历史, 多轮对话能记住上下文
- SessionStore 持久化, 程序重启后能加载历史
"""
import os
import sys

from dotenv import load_dotenv

load_dotenv()

from agent import Agent, Session, SessionStore  # noqa: E402
from agent.tools.builtin import BUILTIN_TOOLS  # noqa: E402
from agent.tools.registry import ToolRegistry  # noqa: E402


if not os.getenv("DEEPSEEK_API_KEY"):
    print("错误: 请在 .env 中设置 DEEPSEEK_API_KEY")
    sys.exit(1)


registry = ToolRegistry()
registry.register_many(BUILTIN_TOOLS)

agent = Agent(registry=registry)
store = SessionStore("/tmp/chat_demo.db")

# 新建一个会话
session = Session(title="多轮对话演示")

print("=== 第一轮 ===")
agent.run(session, "记住一个数字: 42")

print("\n=== 第二轮(测试记忆) ===")
agent.run(session, "我刚才让你记住的数字是多少?")

print("\n=== 第三轮(基于记忆做事) ===")
agent.run(session, "用 calculate 工具算一下那个数字的平方")

# 持久化
store.save(session)
print(f"\n会话已保存: {session.id}")
print(f"Token 用量: {session.usage.summary()}")
print(f"消息条数: {len(session.messages)}")

# 验证可以加载
loaded = store.load(session.id)
assert loaded is not None
assert len(loaded.messages) == len(session.messages)
print(f"\n✓ 重新加载验证通过: {loaded.title}, {len(loaded.messages)} 条消息")

# 在加载的会话上继续聊
print("\n=== 第四轮(在加载的会话上继续) ===")
agent.run(loaded, "那个数字加上 100 是多少?")
store.save(loaded)

# 列出所有会话
print("\n=== 所有会话 ===")
for s in store.list_sessions():
    print(f"  {s['id']} - {s['title']} - {s['message_count']} 条")
