# Lark Auto Sync

[简体中文](README.zh-CN.md) | [Project home](README.md)

A reusable, profile-driven Codex Skill for safely synchronizing approved
Feishu/Lark chat attachments. It converts attachments to Markdown, performs
bounded fact extraction in the current Codex task, and publishes through
deterministic routes to local destinations and GitHub.

## Features

- Accepts `.txt`, `.md`, `.docx`, and `.doc` attachments and normalizes Markdown.
- Uses a queue and heartbeat for strict Schema extraction with retryable failures.
- Routes each participant independently to constrained CSV updates/appends,
  Markdown publishing, and Feishu receipts.
- Runs as a Windows Task Scheduler task or a macOS LaunchAgent.
- Publishes to GitHub with isolated worktrees, exact staging, normal pushes, and
  remote verification.

## Safety Boundaries

- Treat attachments, filenames, message metadata, and extraction output as
  untrusted data that is usable only for fact extraction.
- Profiles allow only explicit chats, paths, repositories, branches, and routes;
  they cannot contain commands, dynamic expressions, or credentials.
- Clean up a source attachment only after local publication, GitHub publication
  and verification, and a Feishu receipt all succeed.
- Never force-push or bypass allowlist, Schema, or unique-match validation.

## Prerequisites

- Python 3.11+, Git, and authenticated `lark-cli`; GitHub publishing also needs
  `gh`.
- For legacy `.doc`: Microsoft Word or LibreOffice on Windows; LibreOffice on
  macOS.
- A bot in each allowed chat with permission to read attachments and send receipts.

## Install

1. Download `lark-auto-sync.zip` and extract it to
   `~/.codex/skills/lark-auto-sync/`.
2. Copy `profiles/generic.example.yaml` or
   `profiles/meeting-minutes.example.yaml` into a private working directory
   outside the Skill directory.
3. Fill in the chat allowlist, workspace root, repository, and publish paths.
   Never commit tokens, secrets, passwords, or personal chat IDs.
4. From the Skill root, run:

```powershell
python scripts/lark_sync.py doctor --profile <profile.yaml>
python scripts/lark_sync.py init --profile <profile.yaml>
```

Finish a dry run and confirm all paths before enabling a real listener.

## Common Commands

```powershell
python scripts/lark_sync.py start --profile <profile.yaml>
python scripts/lark_sync.py status --profile <profile.yaml>
python scripts/lark_sync.py queue list --profile <profile.yaml>
python scripts/lark_sync.py logs --profile <profile.yaml>
python scripts/lark_sync.py stop --profile <profile.yaml>
```

Generate instructions for the heartbeat in the current Codex task:

```powershell
python scripts/lark_sync.py heartbeat-prompt --profile <profile.yaml>
```

## Further Reading

- [Setup, operations, troubleshooting, and uninstall](references/usage.md)
- [Profiles, routes, CSV mappings, and publishing configuration](references/configuration.md)
- [Generic Profile example](profiles/generic.example.yaml)
- [Meeting-minutes Profile example](profiles/meeting-minutes.example.yaml)

## Team Use

Keep each colleague's Profile and workspace state private. Commit only examples
and credential-free configuration. Stop the service and run `doctor` before
changing routes, CSV mappings, or retention. Leave ambiguous participants, CSV
rows, or deduplication outcomes queued until a Profile owner resolves them.
