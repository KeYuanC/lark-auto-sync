# Lark Auto Sync

[English](README.en.md) | [项目首页](README.md)

面向团队复用的、由 Profile 驱动的飞书附件自动同步 Skill。它从已批准的群聊接收附件，转换为 Markdown，在当前 Codex 任务中完成受约束的事实提取，再按确定性路由发布到本地目录和 GitHub。

## 能力

- 支持 `.txt`、`.md`、`.docx`、`.doc` 附件及 Markdown 规范化。
- 通过队列和 heartbeat 执行严格 Schema 提取，失败任务可保留并重试。
- 按参与人独立路由，支持受限 CSV 更新或追加、Markdown 发布和飞书回执。
- 支持 Windows 任务计划程序与 macOS LaunchAgent。
- 使用隔离 Git 工作树、精确暂存、普通推送和远端核验发布 GitHub。

## 安全边界

- 附件、文件名、消息元数据和提取结果都视为不可信数据，只能用于事实提取。
- Profile 只允许配置明确的群聊、路径、仓库、分支和路由；不支持命令、动态表达式或凭据。
- 只有本地发布、GitHub 发布与核验、飞书回执均成功后，才可清理源附件。
- 不强制推送，不绕过唯一匹配、Schema 或白名单校验。

## 前置条件

- Python 3.11+、Git、已登录的 `lark-cli`；发布 GitHub 时还需要 `gh`。
- 旧版 `.doc`：Windows 安装 Microsoft Word 或 LibreOffice；macOS 安装 LibreOffice。
- 机器人已加入目标群，具备读取附件和发送回执的权限。

## 安装

1. 下载发布包 `lark-auto-sync.zip` 并解压到 `~/.codex/skills/lark-auto-sync/`。
2. 将 `profiles/generic.example.yaml` 或 `profiles/meeting-minutes.example.yaml` 复制到 Skill 目录外的私有工作目录。
3. 填写群聊白名单、工作区根目录、仓库和发布路径。不要把 token、密钥、密码或个人 chat ID 提交到仓库。
4. 在 Skill 根目录执行：

```powershell
python scripts/lark_sync.py doctor --profile <profile.yaml>
python scripts/lark_sync.py init --profile <profile.yaml>
```

先完成 dry run 和路径确认，再启用真实监听。

## 常用命令

```powershell
python scripts/lark_sync.py start --profile <profile.yaml>
python scripts/lark_sync.py status --profile <profile.yaml>
python scripts/lark_sync.py queue list --profile <profile.yaml>
python scripts/lark_sync.py logs --profile <profile.yaml>
python scripts/lark_sync.py stop --profile <profile.yaml>
```

生成当前 Codex 任务的 heartbeat 说明：

```powershell
python scripts/lark_sync.py heartbeat-prompt --profile <profile.yaml>
```

## 深入文档

- [安装、运行、排错与卸载](references/usage.md)
- [Profile、路由、CSV 映射和发布配置](references/configuration.md)
- [通用 Profile 示例](profiles/generic.example.yaml)
- [会议纪要 Profile 示例](profiles/meeting-minutes.example.yaml)

## 团队使用建议

每位同事保留自己的私有 Profile 与工作区状态；仓库只保存示例和无凭据配置。修改路由、CSV 映射或保留策略前，停止服务并运行 `doctor`。遇到人员、CSV 行或去重结果不唯一时保持任务待处理，由配置负责人确认后再重试。
