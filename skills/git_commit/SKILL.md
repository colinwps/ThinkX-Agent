---
name: git-commit
description: "生成规范的 Git commit message。当用户要求'写 commit'、'生成提交信息'、或者需要根据代码改动总结提交说明时使用。"
---

# Git Commit Message Skill

## 何时使用
- 用户说"帮我写一个 commit message"
- 用户给了 git diff 或描述了改动, 要求总结
- 用户问"这个改动该怎么写 commit"

## Commit 格式规范(Conventional Commits)

```
<type>(<scope>): <subject>

<body>

<footer>
```

### type 取值
- `feat`: 新功能
- `fix`: 修 bug
- `docs`: 文档
- `style`: 格式(不影响逻辑)
- `refactor`: 重构
- `test`: 测试
- `chore`: 构建/工具链

### subject 规则
- 不超过 50 个字符
- 用动词原形开头(add/fix/update, 不是 added/fixing)
- 末尾不加句号

## 工作流程
1. 如果用户没给 diff, 先用 `run_shell` 执行 `git diff --staged` 看暂存改动
2. 根据改动判断 type 和 scope
3. 生成 commit message
4. 直接用文本回复给用户, **不要**自动 `git commit`

## 示例
```
feat(auth): add JWT refresh token support

- Implement /api/auth/refresh endpoint
- Token TTL extended to 7 days
- Add unit tests for token rotation
```
