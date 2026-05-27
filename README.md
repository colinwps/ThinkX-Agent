# my-agent (v5: WebUI 演示)

从零实现的最小 Agent 框架。

## 版本演进

- **v1**: ReAct 主循环 + Tool 抽象 + MCP + Skill
- **v2**: 会话化 + SQLite 持久化 + 流式输出 + 工具审批 + CLI REPL
- **v3**: Trace/Span 可观测性 + 成本统计 + 脱敏 + 查看工具
- **v4**: 稳健性子系统 + 真实业务接入(图书馆)
- **v5**: ★ **React WebUI + 浏览器审批弹框**

## v5 新增

### React WebUI (`webui/`)
- ✅ **单 HTML 文件** - CDN 引入 React 18 + Babel, 零 build
- ✅ **流式打字效果** - SSE 实时推送, 字符逐个到达
- ✅ **工具调用可视化** - 折叠/展开查看参数和结果
- ✅ **会话管理** - 侧边栏列表 + 自动保存
- ✅ **Token/成本实时统计** - 右上角和侧边栏底部
- ✅ **工具面板** - 右抽屉看所有已注册工具
- ✅ **审批弹框** - 危险工具弹模态框, 允许/拒绝(带理由)/超时

### 审批桥接器 (`webui/approval_bridge.py`)
- ✅ 跨线程通信: Agent 工作线程 ↔ HTTP 请求
- ✅ `threading.Event` 信号 + 超时保护
- ✅ 并发安全: 同时多个 pending 互不干扰

## 项目结构

```
my-agent/
├── agent/                           # v1-v4 的核心(完全复用)
│   ├── core.py / session.py / cli.py / ...
│   ├── tools/ skills/ observability/ robustness/
├── mcp_servers/                     # v4 的图书馆 MCP server
│   ├── demo_server.py
│   └── library/
├── webui/                           # ★ v5 新增
│   ├── server.py                    #   FastAPI + SSE
│   ├── approval_bridge.py           #   审批跨线程桥
│   ├── static/index.html            #   单文件 React 应用
│   └── README.md
├── examples/                        # 10 个示例 (CLI 演示)
├── skills/
├── trace_cli.py
├── requirements.txt
└── README.md
```

## 快速开始

### 用浏览器对话 (v5 新增)

```bash
pip install -r requirements.txt
cp .env.example .env  # 填 DEEPSEEK_API_KEY

python -m webui.server
# 浏览器打开 http://localhost:8765
```

### 用 CLI 对话 (v2-v4 已有)

```bash
python -m examples.07_repl              # 通用 REPL
python -m examples.10_library_agent     # 图书馆 REPL
```

### 跑示例

```bash
python -m examples.09_robustness        # 稳健性能力演示
python trace_cli.py list                 # 查 trace
```

## 推荐学习顺序

按 example 数字 → webui → 业务接入:

1. `01_minimal_loop.py` - **看清 Agent 本质**就是 `while + tool 调用`
2. `02_with_tools.py` - 抽象的 Tool 系统
3. `03_with_mcp.py` - 怎么把工具"外置"成 MCP
4. `04_with_skills.py` - 按需加载的方法论
5. `05_chat.py` - 会话化(v2)
6. `06_streaming.py` - 流式输出(v2)
7. `07_repl.py` - CLI REPL + 可观测性(v2+v3)
8. `08_tracing.py` - 单独看 trace 系统(v3)
9. `09_robustness.py` - 稳健性子系统(v4)
10. `10_library_agent.py` - 真实业务(v4)
11. **`webui/`** - 浏览器版本(v5)

## 全部能力总览

```
✓ 模型: DeepSeek/通义/智谱/Kimi 任意切换
✓ 工具: 本地 + MCP 双源, schema 自动生成
✓ Skill: 按需加载的方法论
✓ 会话: SQLite 持久化, 流式输出
✓ 安全: 工具审批(CLI + 浏览器弹框), 命令黑名单, 结果护栏
✓ 观测: 完整 trace + token/成本 + 脱敏 + Rich UI 查看
✓ 稳健: LLM/MCP 自动重试 + 退避, 超时
✓ 业务: 完整图书馆查询助手 (6 个 MCP 工具)
✓ 界面: CLI REPL + React WebUI 双端
```

## 文件清单

50 个 Python 文件 + 1 个 HTML + 6 个 markdown:

```
agent/                  17 个 py 文件
mcp_servers/            5 个 py 文件
skills/                 1 个 py 文件 + 2 个 SKILL.md
examples/               10 个 py 文件
webui/                  3 个 py 文件 + 1 个 html
trace_cli.py            1 个 py 文件
```
