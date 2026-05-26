# ThinkX-Agent (ColinApp)

从零实现的最小 Agent 框架。


### 稳健性子系统 (`agent/robustness/`)
- ✅ **工具结果护栏** - 超长输出自动截断, 中间省略给模型明确提示
- ✅ **Shell 命令黑名单** - 拦截 `rm -rf /`、`mkfs.*`、`curl | bash` 等明显恶意命令
- ✅ **超时控制** - 线程级超时, 超时后任由后台跑完(不强杀)
- ✅ **重试 + 退避** - 指数退避 + 抖动, 区分可重试/不可重试错误
- ✅ **LLM 客户端外壳** - `ResilientOpenAI` 自动重试网络错误
- ✅ **MCP 客户端重试** - MCPClient 接受 retry_policy, IO 抖动自动恢复

### 业务接入: 智能图书馆查询助手 (`mcp_servers/library/`)
- ✅ SQLite 数据库 + 种子数据(12 本图书、5 读者、8 借阅记录)
- ✅ 6 个 MCP 查询工具(只读): 图书/读者/借阅/超期/统计
- ✅ Server 端手机号自动脱敏(138****8001)
- ✅ 完整集成所有子系统: trace、护栏、重试、审批

## 项目结构

```
ThinkX-Agent/
├── agent/
│   ├── core.py                       # Agent 主循环
│   ├── session.py / streaming.py / approval.py / cli.py / __init__.py
│   ├── tools/
│   │   ├── base.py / registry.py / builtin.py
│   │   └── mcp_client.py             
│   ├── skills/
│   ├── observability/                
│   └── robustness/                   
│       ├── guards.py                 #   ToolResultGuard + CommandValidator
│       ├── timeout.py                #   超时控制
│       ├── retry.py                  #   RetryPolicy + LLM/MCP 策略
│       └── llm_client.py             #   ResilientOpenAI
├── mcp_servers/
│   ├── demo_server.py
│   └── library/                      
│       ├── db.py                     #   SQLite + DAO (只读)
│       ├── seed.py                   #   种子数据生成
│       └── server.py                 #   FastMCP server (6 工具)
├── skills/
├── examples/
│   ├── 01_minimal_loop.py … 08_tracing.py
│   ├── 09_robustness.py              # ★ v4: 稳健性单点演示
│   └── 10_library_agent.py           # ★ v4: 图书馆 REPL 实战
└── trace_cli.py
```

## 快速开始

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # 填 DEEPSEEK_API_KEY

# 1. 看稳健性单点能力(不需要 LLM)
python -m examples.09_robustness

# 2. 体验业务 Agent: 图书馆查询助手
python -m examples.10_library_agent
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


### 工具结果护栏

```python
from agent.robustness import ToolResultGuard, guard_tool

guard = ToolResultGuard(max_chars=8000)
safe_tool = guard_tool(my_tool, guard)
# 超过 8000 字符 -> 自动截断 + 给模型提示
```

### Shell 命令黑名单

```python
from agent.robustness import CommandValidator
from agent.robustness.guards import make_safe_shell_tool

safe_shell = make_safe_shell_tool(run_shell, CommandValidator.default())
# rm -rf / 等会被拒绝
```

### LLM 自动重试

```python
from agent import Agent
from agent.robustness import default_llm_retry_policy

agent = Agent(
    registry=registry,
    retry_policy=default_llm_retry_policy(),  # 不传也行, Agent 默认就装
)
# 网络抖动 / 429 / 5xx 自动重试 3 次, 指数退避
```

### MCP 自动重试

```python
from agent.tools.mcp_client import MCPClient
from agent.robustness import default_mcp_retry_policy

mcp = MCPClient(
    command="python",
    args=["server.py"],
    retry_policy=default_mcp_retry_policy(),
    env=os.environ.copy(),  # ⚠ MCP SDK 默认不传父进程环境
)
```

### 自定义重试策略

```python
from agent.robustness import RetryPolicy

custom = RetryPolicy(
    max_attempts=5,
    base_delay=0.5,
    max_delay=10.0,
    retry_on=lambda e: isinstance(e, ConnectionError),
    on_retry=lambda attempt, err, delay: log_to_my_system(...),
)
```

## 图书馆 Agent: 可以这样问

```
> 查一下三体这本书还有库存吗
> 技术类有什么书
> 13800138001 是谁? 他现在借了什么? 有罚款吗?
> 读者 R002 借过哪些书
> 全馆现在有哪些书超期没还
> 图书馆最热门的书是哪本
```

模型会自己规划查询路径, 每一步都被 trace 记录, 敏感信息自动脱敏。

