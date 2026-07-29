# Configuration Reference

Each YAML Profile declares one isolated synchronization workflow. Copy an example
outside the Skill directory, replace its placeholders, and run `doctor` before
starting it. Profiles contain locations and allowlists, never passwords, API keys,
client secrets, access tokens, or executable commands.

## Top-Level Fields

| Field | Required | Meaning |
| --- | --- | --- |
| `profile.id` | yes | Unique safe identifier used for profile-local state and the service name. Use letters, digits, `_`, and `-` only. |
| `profile.version` | yes | Profile grammar version. This release accepts `1`. |
| `workspace.root` | yes | Relative workspace directory, resolved from the Profile file. It is the containment boundary for every configured local path. |
| `workspace.state_directory` | no | Relative directory for staging, queue, locks, logs, and isolated publishing worktrees. |
| `source` | yes | Allowed chats and attachment intake settings. |
| `processing` | no | Markdown conversion, extraction, participant, and normalization settings. |
| `routes` | no | Ordered, declarative route rules. They cannot run scripts or expressions. |
| `publish` | no | Local, GitHub, and receipt destinations. |
| `retention` | no | Cleanup behavior for completed and failed jobs. |

## Source And Processing

`source.chat_ids` is a non-empty allowlist of Feishu chat IDs. `source.bot_name`
is the exact mention used to pair an attachment with a request.
`source.mention_window_seconds` controls the same-sender, same-chat pairing window.
`source.attachment_types` may contain only `txt`, `md`, `docx`, and `doc`.
`source.max_attachment_mb` rejects larger attachments before download.

Set `processing.converter` to `markdown`. `processing.extraction_schema`,
`processing.aliases`, and `processing.terminology` are relative paths inside
`workspace.root`; they must not escape it. `processing.participant_sources` may
contain `filename` and/or `h1`. When set, extracted participants must be explicit
in those selected sources. `processing.deduplication.normalize_names`,
`normalize_terms`, and `compare_facts` are booleans that enable the corresponding
comparison steps.

## Routes

Each route has a unique `id`, a `match`, and an `action`. `match` is one predicate
or a non-empty list; every predicate in a list must pass. Supported predicates are:

| Predicate | Required argument | Matches when |
| --- | --- | --- |
| `always` | none | The route always applies. |
| `filename_contains` | `value` | The source filename contains `value`, case-insensitively. |
| `participant_in_filename` | none | At least one extracted participant occurs in the filename. |
| `csv_unique_row` | none | This route has exactly one prepared CSV match. |
| `csv_row_missing` | none | This route has no prepared CSV match. |
| `field_complete` | `field` | The named extracted string field is non-empty. |
| `previous_owner_equals` | `value` | The prepared row owner equals `value`. |

Allowed `action.adapter` values are `csv_update`, `csv_append`, `local_publish`,
`github_publish`, and `lark_receipt`. A route may include `action.config`, which
is a relative, workspace-contained path to that adapter's approved mapping. Do not
put a command, code, dynamic expression, absolute path, or `..` path in a route.

## Publishing And Receipts

`publish.local.directory` is a relative destination under the workspace.
`publish.github.repository` uses `owner/repository`, `publish.github.branch` is
the permitted branch, and `publish.github.path` is a relative path within that
repository. The publisher stages only its allowlisted output paths, uses a fresh
worktree, fetches the remote immediately before publishing, and never force pushes.

`publish.lark_receipt.enabled` controls replies. Its `template` is a relative,
workspace-contained UTF-8 text file. The file may use only these placeholders:
`{{filename}}`, `{{local_destinations}}`, `{{github_destinations}}`,
`{{routes}}`, `{{paused_participants}}`, and `{{commit}}`. Unknown placeholders
are rejected. A receipt must name the destinations actually published.

`retention.delete_source_after_verified_publish` permits cleanup only after local
publication, GitHub publication and remote verification, and the Feishu receipt all
succeed. `retention.failed_job_days` is the retention period for failed jobs.

## Containment And Upgrades

All paths in a Profile are relative to `workspace.root`, including route configs.
The loader rejects absolute paths and paths outside that workspace. The Profile file
itself may not define credentials or arbitrary commands.

Before changing a Profile version, stop the profile service, back up the Profile and
its workspace state, read the release notes, migrate it with the provided command,
and run `doctor` plus a dry run. Do not manually change `profile.version` merely to
bypass validation. Re-enable the service only after the migrated Profile validates.
