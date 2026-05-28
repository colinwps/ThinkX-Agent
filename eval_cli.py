#!/usr/bin/env python3
"""
eval_cli.py - 命令行 eval 工具

用法:
    # 跑一组 case (single file 或 directory)
    python eval_cli.py run evals/library/basic_queries.yaml
    python eval_cli.py run evals/library/         # 整个目录所有 yaml

    # 看历史 runs
    python eval_cli.py list

    # 看某个 run 详情
    python eval_cli.py show <run_id>

    # A/B 对比
    python eval_cli.py compare <run_a> <run_b>

    # 加上 --plan / --reflect / --parallel 来跑不同配置 (做 A/B)
    python eval_cli.py run evals/library/basic_queries.yaml --plan
"""
import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def cmd_run(args):
    """跑 eval"""
    if not os.getenv("DEEPSEEK_API_KEY"):
        print("❌ 错误: 请先在 .env 中设置 DEEPSEEK_API_KEY")
        sys.exit(1)

    # 准备图书馆 server 和 agent (复用 webui 的初始化代码片段)
    from rich.console import Console
    console = Console()

    console.print("[dim]准备图书馆数据...[/]")
    sys.path.insert(0, str(Path(__file__).parent))
    from mcp_servers.library.seed import seed
    LIBRARY_DB = "/tmp/library.db"
    seed(LIBRARY_DB, reset=True)

    os.environ["LIBRARY_DB_PATH"] = LIBRARY_DB
    from agent.tools.mcp_client import MCPClient
    from agent.robustness.retry import default_mcp_retry_policy

    project_root = Path(__file__).parent
    library_mcp = MCPClient(
        command=sys.executable,
        args=[str(project_root / "mcp_servers" / "library" / "server.py")],
        name="library",
        retry_policy=default_mcp_retry_policy(),
        env=os.environ.copy(),
    )
    library_mcp.start()
    console.print(f"[dim]MCP 工具: {[t['name'] for t in library_mcp.list_tools()]}[/]")

    # 工具注册
    from agent.tools.builtin import calculate, list_dir, read_file
    from agent.tools.registry import ToolRegistry
    from agent.robustness import ToolResultGuard, guard_tool

    registry = ToolRegistry()
    guard = ToolResultGuard(max_chars=8000)
    registry.register(guard_tool(read_file, guard))
    registry.register(guard_tool(list_dir, guard))
    registry.register(calculate)
    for mcp_tool in library_mcp.to_tools():
        registry.register(guard_tool(mcp_tool, guard))

    # 组装 Agent
    from agent import Agent
    from agent.approval import ApprovalPolicy

    agent_kwargs = {
        "registry": registry,
        "approval_policy": ApprovalPolicy.yolo(),  # eval 模式: 自动通过
    }
    if args.plan:
        from agent.strategies.plan import make_default_planner
        tmp = Agent(registry=registry)
        agent_kwargs["plan_first"] = True
        agent_kwargs["planner"] = make_default_planner(tmp.client, "deepseek-chat")
    if args.reflect:
        from agent.strategies.reflect import make_default_reflector
        tmp = Agent(registry=registry)
        agent_kwargs["reflect"] = True
        agent_kwargs["reflector"] = make_default_reflector(tmp.client, "deepseek-chat")
        agent_kwargs["reflect_threshold"] = args.reflect_threshold
    if args.parallel:
        agent_kwargs["parallel_tools"] = True
    if args.model:
        agent_kwargs["model"] = args.model

    agent = Agent(**agent_kwargs)

    # Judge
    from agent.eval.judge import make_judge_fn
    judge_fn = make_judge_fn(agent.client, args.judge_model)

    # Runner + Store
    from agent.eval.runner import EvalRunner
    from agent.eval.store import EvalStore
    from agent.eval.viewer import show_run_summary, show_case_detail
    from agent.eval.case import load_suite, load_suites_in_dir

    runner = EvalRunner(agent=agent, judge_fn=judge_fn)
    store = EvalStore()

    # 加载 cases
    p = Path(args.cases)
    if p.is_file():
        suites = [load_suite(p)]
    elif p.is_dir():
        suites = load_suites_in_dir(p)
    else:
        console.print(f"[red]路径不存在: {p}[/]")
        sys.exit(1)

    total_cases = sum(len(s) for s in suites)
    console.print(f"\n找到 {len(suites)} 个 suite, 共 {total_cases} 个 case\n")

    all_results = []
    for suite in suites:
        console.rule(f"[bold]Suite: {suite.name}[/]")
        if args.tag:
            # 过滤 tag
            filtered = [c for c in suite.cases if args.tag in c.tags]
            if not filtered:
                console.print(f"[dim](无匹配 tag={args.tag} 的 case, 跳过)[/]")
                continue
            from agent.eval.case import CaseSuite
            suite = CaseSuite(name=suite.name, description=suite.description,
                              cases=filtered, source=suite.source)

        # 每跑完一个 case 实时显示进度
        def on_done(case, cr):
            mark = "[green]✓[/]" if cr.passed else "[red]✗[/]"
            console.print(f"  {mark} {case.id}: "
                          f"{cr.pass_count}/{cr.total_assertions} asserts, "
                          f"{cr.duration_seconds:.1f}s")

        result = runner.run_suite(suite, on_case_done=on_done)
        store.save_run(result)
        all_results.append(result)
        console.print()
        show_run_summary(result, console)
        console.print()

        # 如果有失败的, 默认显示详情
        if args.verbose or any(not c.passed for c in result.cases):
            for cr in result.cases:
                if not cr.passed or args.verbose:
                    show_case_detail(cr, console)

    # 总结
    if len(all_results) > 1:
        total_pass = sum(r.pass_count for r in all_results)
        total_all = sum(r.total_count for r in all_results)
        console.rule(f"[bold]全部 Suite 总计: {total_pass}/{total_all} 通过[/]")

    library_mcp.stop()


def cmd_list(args):
    from rich.console import Console
    from agent.eval.store import EvalStore
    from agent.eval.viewer import show_run_table
    console = Console()
    store = EvalStore()
    runs = store.list_runs(suite_name=args.suite, limit=args.n)
    show_run_table(runs, console)


def cmd_show(args):
    from rich.console import Console
    from agent.eval.store import EvalStore
    console = Console()
    store = EvalStore()
    run = store.get_run(args.run_id)
    if run is None:
        console.print(f"[red]找不到 run: {args.run_id}[/]")
        sys.exit(1)
    import json
    console.print(f"[bold]Run {run['run_id']}[/]")
    console.print(f"  Suite: {run['suite_name']}")
    console.print(f"  通过: {run['pass_count']}/{run['total_count']}")
    console.print(f"  成本: ${run['total_cost_usd']:.4f}")
    config = json.loads(run["config_json"])
    console.print(f"  配置: {config}")
    console.print()
    for c in run["cases"]:
        mark = "[green]✓[/]" if c["passed"] else "[red]✗[/]"
        console.print(f"  {mark} {c['case_id']} - {c['duration_s']:.1f}s, "
                      f"${c['cost_usd']:.4f}")


def cmd_compare(args):
    from rich.console import Console
    from agent.eval.store import EvalStore
    from agent.eval.viewer import show_compare
    console = Console()
    store = EvalStore()
    diff = store.compare_runs(args.run_a, args.run_b)
    show_compare(diff, console)


def main():
    parser = argparse.ArgumentParser(description="my-agent eval 工具")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="跑一组 case")
    p_run.add_argument("cases", help="单个 yaml 文件 或 目录")
    p_run.add_argument("--plan", action="store_true", help="开启 plan_first")
    p_run.add_argument("--reflect", action="store_true", help="开启 reflect")
    p_run.add_argument("--reflect-threshold", type=int, default=7)
    p_run.add_argument("--parallel", action="store_true", help="开启 parallel_tools")
    p_run.add_argument("--model", help="模型名 (默认 deepseek-chat)")
    p_run.add_argument("--judge-model", default="deepseek-chat", help="LLM judge 用的模型")
    p_run.add_argument("--tag", help="只跑含此 tag 的 case")
    p_run.add_argument("--verbose", "-v", action="store_true", help="显示所有 case 详情")

    p_list = sub.add_parser("list", help="列出历史 runs")
    p_list.add_argument("--suite", help="按 suite 名过滤")
    p_list.add_argument("-n", type=int, default=20)

    p_show = sub.add_parser("show", help="看某 run 详情")
    p_show.add_argument("run_id")

    p_cmp = sub.add_parser("compare", help="A/B 对比两个 run")
    p_cmp.add_argument("run_a")
    p_cmp.add_argument("run_b")

    args = parser.parse_args()

    if args.cmd == "run":
        cmd_run(args)
    elif args.cmd == "list":
        cmd_list(args)
    elif args.cmd == "show":
        cmd_show(args)
    elif args.cmd == "compare":
        cmd_compare(args)


if __name__ == "__main__":
    main()
