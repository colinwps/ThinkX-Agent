# webui: React 对话演示 (含审批弹框)

基于 v4 的能力, 在浏览器里和 Agent 对话。

## 截图(文字版)

```
┌──────────────────────┬─────────────────────────────────────┐
│ 🤖 my-agent          │ 会话 11-15 14:30   2次调用·140 tokens│
│ 图书馆查询助手 · v5  ├─────────────────────────────────────┤
│ [+ 新建会话]         │                                      │
├──────────────────────┤  👤 把统计写到 /tmp/x.txt            │
│ ▸ 会话 11-15 14:30   │  ──────────────────────             │
│   会话 11-15 13:22   │  🔧 library_stats() ▸               │
│   ...                │  🔧 write_file({...}) 运行中…       │
│                      │                                      │
│                      │   ┌─ ⚠ 需要确认 ─────────────┐      │
│                      │   │ 即将执行: write_file      │      │
│                      │   │ 参数: { path: "/tmp/..." }│      │
│                      │   │ [拒绝]  [允许执行]        │      │
│                      │   │ ⏱ 95s 自动拒绝            │      │
│                      │   └───────────────────────────┘      │
└──────────────────────┴─────────────────────────────────────┘
```

## 启动

```bash
pip install -r requirements.txt
cp .env.example .env  # 填 DEEPSEEK_API_KEY

python -m webui.server
# 浏览器打开 http://localhost:8765
```

## 特性

- ✅ **流式输出** - 模型边想边显示, 字符逐个到达
- ✅ **工具调用可视化** - 折叠/展开看参数和结果
- ✅ **审批弹框** - 危险工具(`run_shell`/`write_file`)执行前弹框确认
  - 允许 / 拒绝(可附理由) / 120s 自动超时
  - 拒绝理由会传给模型, 模型能换路或停止
- ✅ **会话管理** - 侧边栏列表, 自动持久化到 SQLite
- ✅ **Token & 成本实时统计**
- ✅ **工具面板** - 看所有已注册工具(本地 + MCP)
- ✅ **图书馆业务接入** - 默认接入查图书/读者/借阅 6 个 MCP 工具

## 技术栈

- **后端**: FastAPI + uvicorn (Python)
  - 全套 `agent` 模块 (v1-v4 的能力)
  - SSE (Server-Sent Events) 推流
  - **跨线程审批桥**: `ApprovalBridge` + `threading.Event` + `queue.Queue`
- **前端**: React 18 + Babel (浏览器编译 JSX)
  - 单 HTML 文件, CDN 引入, 零 build
  - 原生 fetch 解析 SSE (不用 EventSource 因为要 POST)

## 文件结构

```
webui/
├── server.py            # FastAPI 后端
├── approval_bridge.py   # 审批桥: agent 线程 ↔ HTTP 请求
├── static/
│   └── index.html       # 单文件 React 应用
└── README.md
```

## SSE 事件类型

POST `/api/sessions/{id}/messages` 返回 SSE, 每帧形如:

```
event: text
data: {"chunk": "你好"}

event: tool_call
data: {"id": "c1", "name": "search_books", "arguments": {...}}

event: approval_required          ← 危险工具触发, 浏览器弹框
data: {"id": "abc123", "tool_name": "write_file", "arguments": {...}, "timeout": 120}

event: tool_result
data: {"id": "c1", "name": "search_books", "result": "...", "approved": true}

event: approval_timeout           ← 用户没及时点 (后端自动拒绝)
data: {"id": "abc123"}

event: done
data: {"final": "完整回答", "usage": {...}}
```

完整事件: `user_message` / `iteration` / `text` / `tool_call` / `tool_result` / `approval_required` / `approval_timeout` / `done` / `aborted` / `error`

## 审批工作流

```
浏览器                    后端 SSE                Agent 线程
   │                         │                       │
   │ POST /messages          │                       │
   ├────────────────────────→│                       │
   │                         │ 启 agent worker 线程  │
   │                         ├──────────────────────→│
   │ ← SSE: tool_call ────── │ ← event_queue ──────  │ Agent 准备调 write_file
   │                         │                       │ 调 approval_callback
   │                         │                       │   ↓
   │                         │                       │ ApprovalBridge.await_decision
   │                         │                       │   - 生成 approval_id
   │                         │                       │   - push_event → queue
   │ ← SSE: approval_required│                       │   - 阻塞 wait
   │                         │                       │
   │ [弹框, 用户点允许]      │                       │
   │ POST /approvals/{id}    │                       │
   ├────────────────────────→│                       │
   │                         │ bridge.resolve()      │
   │                         │   set Event           │
   │                         │                       │ wait 返回 (True, '')
   │                         │                       │ 实际执行工具
   │                         │ ← event_queue ──────  │
   │ ← SSE: tool_result ──── │                       │
```

## API 端点

| 路径 | 方法 | 说明 |
|---|---|---|
| `/` | GET | HTML 主页 |
| `/api/sessions` | GET/POST | 列出/新建会话 |
| `/api/sessions/{id}` | GET/DELETE | 加载/删除会话 |
| `/api/sessions/{id}/messages` | POST | **SSE 流式发消息** |
| `/api/approvals/{id}` | POST | **提交审批决定** |
| `/api/approvals/pending` | GET | 列出待处理审批(调试用) |
| `/api/tools` | GET | 工具列表 |
| `/api/stats` | GET | 累计统计 |
| `/api/traces` | GET | trace 列表 |

## 当前限制

1. **Agent 全局共享** - 高并发请求会互相覆盖 approval_callback
   - 生产场景应该 per-request 构造 Agent, 或者用 contextvars 隔离
2. **不支持多用户** - sessions.db 全局共享
3. **审批是单步的** - 一次只显示一个 modal; 多个并行的 tool_call 会逐个弹
4. **CORS 全开** - 仅适合本地开发
5. **断线无恢复** - 网络断了 SSE 流终止, 会话仍在但要重新发

## 部署到生产前要做的事

如果想真上线:
- [ ] CORS 限制到具体域名
- [ ] 加用户认证 + 会话隔离
- [ ] 用 WebSocket 替换 SSE (双向 + 心跳)
- [ ] Agent 实例 per-user 隔离
- [ ] Token/费用配额
- [ ] 持久化审批日志 (谁批准了什么)
