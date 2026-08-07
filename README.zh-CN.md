# Lark Auto Sync · 飞书自动同步

[English](README.en.md) | [查看完整中文 README](README.md)

完整的中文项目说明、工作流、使用场景、安装步骤与安全边界已迁移至 [README.md](README.md)。

## 可靠性补充

监听器健康、飞书历史回补、Codex 队列和 GitHub 发布必须分开核对。每次 heartbeat 先检查 `status`，必要时运行 `start`，再运行 `scan` 回补监听停止期间的历史附件，最后才运行 `queue list`。队列为空不能单独证明飞书没有新记录；任务、发布或回执失败时必须保留源附件并等待重试。
