"""
Case 数据模型 + YAML 加载

YAML 格式示例:
---
name: basic_queries
description: 基础查询场景
cases:
  - id: search_book
    input: "查一下《三体》"
    asserts:
      - type: tool_called
        name: search_books
        args:
          query: "三体"
      - type: contains
        text: "刘慈欣"

  - id: get_reader
    input: "13800138001 是谁"
    asserts:
      - type: tool_called
        name: find_reader
      - type: contains
        text: "张三"
"""
from __future__ import annotations

import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Case:
    """单个测试场景"""
    id: str
    input: str
    asserts: list[dict] = field(default_factory=list)
    description: str = ""
    tags: list[str] = field(default_factory=list)
    # 可选: 给这个 case 单独的 system prompt
    system_prompt: str | None = None
    # 可选: 这个 case 的预期成本/延迟上限 (用于 perf 检测)
    max_iterations: int | None = None


@dataclass
class CaseSuite:
    """一组 case (= 一个 YAML 文件)"""
    name: str
    description: str = ""
    cases: list[Case] = field(default_factory=list)
    source: str = ""  # 来源文件路径

    def __len__(self) -> int:
        return len(self.cases)


def load_suite(path: str | Path) -> CaseSuite:
    """从 YAML 文件加载一组 case"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"找不到 case 文件: {path}")

    with open(p, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(f"{path} 应该是 dict, 实际 {type(data).__name__}")

    suite = CaseSuite(
        name=data.get("name", p.stem),
        description=data.get("description", ""),
        source=str(p),
    )

    raw_cases = data.get("cases", [])
    if not isinstance(raw_cases, list):
        raise ValueError(f"{path}: cases 应该是 list")

    for i, rc in enumerate(raw_cases):
        if not isinstance(rc, dict):
            raise ValueError(f"{path} cases[{i}] 应该是 dict")
        if "input" not in rc:
            raise ValueError(f"{path} cases[{i}] 缺 input 字段")

        case = Case(
            id=rc.get("id", f"case_{i}"),
            input=rc["input"],
            asserts=rc.get("asserts", []),
            description=rc.get("description", ""),
            tags=rc.get("tags", []),
            system_prompt=rc.get("system_prompt"),
            max_iterations=rc.get("max_iterations"),
        )
        suite.cases.append(case)

    return suite


def load_suites_in_dir(directory: str | Path) -> list[CaseSuite]:
    """加载一个目录下所有 .yaml 文件"""
    d = Path(directory)
    if not d.exists() or not d.is_dir():
        raise NotADirectoryError(str(d))
    suites = []
    for f in sorted(d.glob("*.yaml")):
        try:
            suites.append(load_suite(f))
        except Exception as e:
            import sys
            print(f"[warn] 跳过 {f}: {e}", file=sys.stderr)
    return suites
