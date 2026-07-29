# Lark Auto Sync README Refresh Design

## Goal

Replace the sparse repository README with a bilingual GitHub project homepage that
quickly explains the Skill's purpose, safety boundary, workflow, and first-use
path for colleagues.

## Audience

Team members who need to install or operate a reusable Feishu/Lark attachment
synchronization Skill without receiving credentials, personal chat identifiers,
or a production Profile.

## Information Architecture

`README.md` is the Chinese-first landing page. It has a centered brand block,
verifiable Shields.io badges, Chinese/English links, a concise positioning
statement, and these sections in order:

1. Why use it.
2. End-to-end Mermaid workflow.
3. Supported use cases.
4. Three-minute quick start.
5. Core safety guarantees.
6. Configuration and operations navigation.
7. Verification and project structure.

`README.en.md` is a complete English mirror with the same section order and
reciprocal language links. `README.zh-CN.md` becomes a short Chinese entry point
that sends visitors to the root README, preventing a third long document from
drifting out of sync.

## Visual Language

Use GitHub-native Markdown only: a centered title block, `flat-square` badges,
short paragraphs, restrained tables, horizontal rules, and Mermaid. Do not add
custom images, external tracking, credentials, claims that cannot be checked from
the repository, or copied wording/assets from the reference project.

## Content Boundaries

Document only shipped features: approved attachment intake, Markdown conversion,
current-task Codex extraction, deterministic routes, CSV mappings, local/GitHub
publication, Feishu receipts, queue recovery, and Windows/macOS services.
State clearly that Profiles must remain private and credential-free in version
control. Link to the existing examples and detailed references instead of
duplicating their full configuration grammar.

## Validation

Run Markdown link checks for local references, `git diff --check`, the full unit
suite, Skill validation, and the package command. Verify the regenerated ZIP
contains all three README files and excludes state, cache, Git metadata, and
credential-like paths.
