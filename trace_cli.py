#!/usr/bin/env python3
"""
独立的 trace 查看工具(不进 REPL)

用法:
    python trace_cli.py list                  # 列出最近 trace
    python trace_cli.py list -n 50            # 多列点
    python trace_cli.py show <trace_id>       # 看 trace 详情
    python trace_cli.py span <span_id>        # 看 span 详情
    python trace_cli.py summary               # 累计统计
    python trace_cli.py --db ~/path.db ...    # 指定数据库
"""
import argparse
import sys
from pathlib import Path

from rich.console import Console

# 让脚本可以从项目根目录直接跑
sys.path.insert(0, str(Path(__file__).parent))

from agent.observability import TraceStore, list_traces, show_span, show_summary, show_trace


def main():
    parser = argparse.ArgumentParser(description="my-agent trace 查看工具")
    parser.add_argument("--db", default="~/.my-agent/sessions.db",
                        help="数据库路径 (默认 ~/.my-agent/sessions.db)")

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="列出 trace")
    p_list.add_argument("-n", type=int, default=20, help="数量 (默认 20)")
    p_list.add_argument("--session", help="按 session_id 过滤")

    p_show = sub.add_parser("show", help="查看 trace 详情")
    p_show.add_argument("trace_id")

    p_span = sub.add_parser("span", help="查看 span 详情")
    p_span.add_argument("span_id")

    sub.add_parser("summary", help="累计统计")

    args = parser.parse_args()

    console = Console()
    store = TraceStore(args.db)

    if args.cmd == "list":
        list_traces(store, console, limit=args.n, session_id=args.session)
    elif args.cmd == "show":
        show_trace(store, console, args.trace_id)
    elif args.cmd == "span":
        show_span(store, console, args.span_id)
    elif args.cmd == "summary":
        show_summary(store, console)


if __name__ == "__main__":
    main()
