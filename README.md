# ThinkX-Agent (v8: 评估体系)

从零实现的最小 Agent 框架。

## 版本演进

- **v1**: ReAct + Tool + MCP + Skill
- **v2**: 会话化 + SQLite + 流式 + REPL
- **v3**: Trace/Span 可观测性 + 成本/脱敏
- **v4**: 稳健性 + 业务接入(图书馆)
- **v5**: React WebUI + 审批弹框
- **v6**: 上下文管理(token 估算 + 历史压缩)
- **v7**: Plan-and-Execute + Reflection + 并行工具 + 多模型 cache
- **v8**: ★ **评估体系: YAML cases + 多种断言 + LLM judge + A/B 对比**

## v8 新增

### `agent/eval/` 子包
- ✅ **YAML case 文件** - 测试场景版本化管理
- ✅ **5 种程序断言** - ToolCalled / NoToolCalled / Contains / NotContains / ToolCallCount
- ✅ **LLM-as-judge** - 主观质量评分(语义/风格类)
- ✅ **SQLite 存储** - 历史 run 持久化, 可对比
- ✅ **A/B 对比** - 找出 regressed / improved / 成本变化的 case

### `eval_cli.py`
命令行入口:
```bash
python eval_cli.py run evals/library/basic_queries.yaml
python eval_cli.py run evals/library/ --plan         # 加 plan 跑
python eval_cli.py list                              # 历史
python eval_cli.py compare <a> <b>                   # A/B
```

### `evals/library/` 图书馆 eval cases
- `basic_queries.yaml` - 5 个单工具场景
- `multi_tool.yaml` - 3 个多工具组合
- `quality.yaml` - 3 个含 LLM judge 的质量场景

## 完整能力总览

```
✓ 模型: DeepSeek/通义/智谱/Kimi
✓ 工具: 本地 + MCP, 串行/并行
✓ Skill: 按需加载
✓ 会话: SQLite + 流式
✓ 安全: CLI + Web 审批, 黑名单, 结果护栏
✓ 观测: trace + 多模型 cache + 节省金额
✓ 稳健: 重试退避超时
✓ 上下文: 压缩 + 预算
✓ 策略: Plan-and-Execute + Reflection
✓ 业务: 图书馆 (6 MCP 工具)
✓ 界面: CLI REPL + React WebUI
✓ 评估: YAML cases + 断言 + LLM judge + A/B 对比
```

## v8 工作流: 用 eval 驱动改造

```bash
# 1. 跑当前配置, 拿 baseline
python eval_cli.py run evals/library/

# 2. 改个东西 (比如开 plan)
python eval_cli.py run evals/library/ --plan

# 3. 对比
python eval_cli.py list   # 看到两个 run_id
python eval_cli.py compare <baseline_id> <new_id>

# 输出:
#   通过率变化: +5.0%
#   成本变化: $+0.0023
#   Case 级:
#     - search_book   same      成本变化 $+0.0008
#     - reader_loans  improved  A: FAIL, B: PASS ✨
#     - hot_books     regressed A: PASS, B: FAIL ⚠
```

**关键价值**: 不再靠"感觉"判断改动好坏, 用客观数据。

## 写自己的 eval

`evals/your_business/your_suite.yaml`:

```yaml
name: my_business
description: 我的业务测试集
cases:
  - id: typical_query
    input: "用户问的典型问题"
    asserts:
      - type: tool_called
        name: search_xxx
        args:
          q: "关键词"
      - type: contains
        text: "预期答案片段"

  - id: edge_case
    input: "边界场景"
    asserts:
      - type: no_tool_called
        name: dangerous_tool
      - type: llm_judge
        criteria: |
          助手的回答是否礼貌地拒绝了越权请求?
        threshold: 7
```

跑: `python eval_cli.py run evals/your_business/`

## 项目文件清单

```
agent/
├── core.py / session.py / approval.py / streaming.py / cli.py
├── tools/ skills/ observability/ robustness/ context/ strategies/
├── parallel.py
└── eval/                            # ★ v8
    ├── case.py / assertions.py / judge.py
    ├── runner.py / store.py / viewer.py
mcp_servers/library/
webui/
examples/01-15
evals/library/                       # ★ v8 业务 cases
eval_cli.py                          # ★ v8
trace_cli.py
```
