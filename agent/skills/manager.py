"""
Skill 管理器
负责: 扫描 skill 目录、把简介注入 system prompt、提供按需加载工具
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import frontmatter


@dataclass
class Skill:
    name: str
    description: str
    path: Path  # SKILL.md 所在目录
    content: str  # SKILL.md 正文(不含 frontmatter)

    @property
    def skill_md_path(self) -> Path:
        return self.path / "SKILL.md"


class SkillManager:
    """
    管理一个 skill 目录下的所有 skill。

    职责:
    1. 启动时扫描所有 SKILL.md
    2. 生成"skill 简介"片段, 注入 system prompt
    3. 按 name 加载完整 skill 内容(给 load_skill 工具用)
    """

    def __init__(self, skills_dir: str | Path):
        self.skills_dir = Path(skills_dir).resolve()
        self.skills: dict[str, Skill] = {}
        self._scan()

    def _scan(self) -> None:
        """递归扫描所有 SKILL.md 文件"""
        if not self.skills_dir.exists():
            return

        for skill_md in self.skills_dir.rglob("SKILL.md"):
            try:
                post = frontmatter.load(skill_md)
                name = post.metadata.get("name")
                description = post.metadata.get("description")

                if not name or not description:
                    print(f"[Skill] 跳过 {skill_md}: 缺少 name 或 description")
                    continue

                self.skills[name] = Skill(
                    name=name,
                    description=description,
                    path=skill_md.parent,
                    content=post.content,
                )
            except Exception as e:
                print(f"[Skill] 解析 {skill_md} 失败: {e}")

    def get(self, name: str) -> Skill | None:
        return self.skills.get(name)

    def all(self) -> list[Skill]:
        return list(self.skills.values())

    # ----- 注入 system prompt 的关键方法 -----

    def render_catalog(self) -> str:
        """
        把所有 skill 的简介渲染成一段文本, 塞进 system prompt
        让模型知道"有哪些技能可用"
        """
        if not self.skills:
            return ""

        lines = [
            "",
            "## 可用 Skills",
            "",
            "你可以使用以下 skill 来帮助完成任务。每个 skill 是一份详细的方法论。",
            "当任务匹配某个 skill 时, **先调用 `load_skill` 工具加载它的完整内容**, 然后按照内容指引去做。",
            "",
        ]
        for skill in self.skills.values():
            lines.append(f"- **{skill.name}**: {skill.description}")
        lines.append("")
        return "\n".join(lines)

    def load_skill_content(self, name: str) -> str:
        """
        加载一个 skill 的完整内容(给 load_skill 工具用)
        返回完整的 markdown 文本, 包含相对路径信息(让模型知道脚本在哪)
        """
        skill = self.get(name)
        if skill is None:
            available = ", ".join(self.skills.keys()) or "(无)"
            return f"[错误] 没找到 skill '{name}'。可用 skill: {available}"

        header = (
            f"# Skill: {skill.name}\n"
            f"目录: {skill.path}\n"
            f"(skill 中提到的脚本路径都是相对于这个目录的, 执行时用完整路径)\n\n"
            f"---\n\n"
        )
        return header + skill.content
