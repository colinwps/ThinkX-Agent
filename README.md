# ThinkX-Agent (会话 + 流式 + REPL)

从零实现的最小 Agent 框架。

## 特性

###  基础能力
- ✅ ReAct 主循环(多轮工具调用)
- ✅ Tool 抽象 + `@tool` 装饰器(Pydantic 自动生成 schema)
- ✅ MCP Client(通过 stdio 接入任意 MCP Server)
- ✅ MCP Server 示例(基于 FastMCP)
- ✅ Skill 系统(按需加载的方法论, 带可执行脚本)
- ✅ 模型无关(DeepSeek / 通义 / 智谱 / Kimi 换 base_url 即可)
- ✅ **Session 会话化**: Agent 无状态, 状态在 Session 里
- ✅ **SQLite 持久化**: 会话历史自动保存, 重启后能恢复
- ✅ **流式输出**: 文字逐字到达, 体验质变
- ✅ **事件流接口**: `run_stream()` 返回 Event 序列, CLI / Web 都能用
- ✅ **工具调用审批**: 危险工具默认要确认 (y/n/a)
- ✅ **CLI REPL**: 完整的命令行交互(历史、流式、确认、斜杠命令)
- ✅ **Token 统计**: 每次会话累计 token 用量

## 项目结构

```
ThinkX-Agent/
├── agent/
│   ├── core.py              # Agent 主循环 (无状态, 流式)
│   ├── session.py           # Session + SessionStore (SQLite)
│   ├── streaming.py         # 流式 chunks 累加器
│   ├── approval.py          # 工具审批策略
│   ├── cli.py               # CLI REPL (斜杠命令、流式渲染、确认)
│   ├── tools/...            # Tools
│   └── skills/...           # Skills
├── mcp_servers/...
├── skills/...
└── examples/
    ├── 01_minimal_loop.py   # 
    ├── 02_with_tools.py     # v1
    ├── 03_with_mcp.py       # v1
    ├── 04_with_skills.py    # v1
    ├── 05_chat.py           # 多轮对话 + 持久化
    ├── 06_streaming.py      # 流式输出
    └── 07_repl.py           # 启动 REPL
```

## 快速开始

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env  # 填入 DEEPSEEK_API_KEY

# 直接进 REPL
python -m examples.07_repl
```

## REPL 内置命令

| 命令 | 说明 |
|---|---|
| `/help` | 显示帮助 |
| `/clear` | 清空当前会话历史 |
| `/new` | 新建会话 |
| `/tokens` | 显示当前会话 token 用量 |
| `/sessions` | 列出所有保存的会话 |
| `/load <id>` | 加载某个会话继续聊 |
| `/title <新标题>` | 重命名当前会话 |
| `/tools` | 列出所有工具(标注哪些需要确认) |
| `/skills` | 列出所有 skill |
| `/yolo` | 全自动模式(危险!) |
| `/careful` | 偏执模式(所有工具都问) |
| `/safe` | 默认安全模式 |
| `/quit`, `/exit`, `/q` | 退出 |

## 工具审批

默认配置("安全模式"):
- `run_shell`, `write_file` → 执行前问 y / n / a
  - `y` = 这次允许
  - `n` = 这次拒绝(可以告诉模型拒绝理由)
  - `a` = 本次会话不再问这个工具
- 其他工具 → 直接放行

可以在 REPL 里随时 `/yolo` 全自动 / `/careful` 全确认。

## 会话持久化

默认存到 `~/.my-agent/sessions.db` (SQLite, 可拷贝可分享)。
每次 REPL 退出 / 工具调用后都会自动保存。
