# Desktop MCP sunset ledger

Status as of 2026-08-04. Companion to the cloud-first direction: the Hub is
a website, the desktop shrinks to a local ingester, and the cloud MCP surface
at `cortex.turfptax.com/mcp` is canonical.

The desktop MCP server (`cortex_mcp/server.py`) exposes **56 tools**. The
cloud exposes **22**. That gap is not a shortfall to close. The locked MCP
redesign deliberately collapsed 40-plus tools to a handful, so this ledger
migrates **capabilities, not tool count**. Anything that dies here dies with
the desktop rather than being ported.

## What changed on 2026-08-04

Two cloud additions closed the gaps that kept the desktop necessary:

- **Notes became reachable.** `cortex_search(kinds="user_note")` and
  `cortex_read("un:<id>")` return the owner's own notes, which were
  previously invisible on the cloud surface. This retires `notes_search`.
- **`cortex_chat` arrived**, with a progress heartbeat and an async
  `cortex_chat_start` / `cortex_chat_result` pair so a 45-70s reply survives
  the caller's ~60s MCP timeout. This retires the desktop `cortex_chat`.

## Retire once the cloud versions are verified live

| Desktop tool | Replaced by | Note |
|---|---|---|
| `cortex_chat` | cloud `cortex_chat` + start/result | The cloud pair also survives a disconnect; the desktop one cannot |
| `cortex_search` | cloud `cortex_search` | Cloud now covers user notes too |
| `cortex_detail` | cloud `cortex_read` | Graph traversal (`next_tokens`) is still a follow-up on the cloud side |
| `notes_search` | cloud `cortex_search(kinds="user_note")` | The desktop version pulls the newest 100 rows and greps in Python, so anything older is unreachable. Do not fix it; delete it |
| `cortex_intro` | cloud server instructions + the intro page | |
| `project_upsert`, `project_list` | `cortex_project_upsert`, `cortex_projects_list` | |
| `cortex_rules`, `cortex_rule_add` | `cortex_rules_list`, `cortex_rule_add` | |
| `cortex_skills`, `cortex_skill_log` | `cortex_skills_list`, `cortex_skill_get`, `cortex_skill_log` | |

## Keep: genuinely needs the local machine

These have no cloud equivalent because the cloud has no access to the
owner's disk, devices, or local agent runtime.

| Tool | Why it stays |
|---|---|
| `send_note` | Offline capture. Writes with `source='ble'` and the active `session_id`; consider stamping `source='desktop'` when next touched, since 'ble' now means "wearable or desktop or web default" and that ambiguity cost us a digest bug |
| `note_update` | No cloud write for note metadata yet |
| `query`, `upsert_row`, `delete_row`, `table_counts` | Raw CMD admin over the local protocol |
| `session_start`, `session_end`, `log_activity`, `log_search`, `log_time` | Local session telemetry |
| `file_register`, `file_list`, `file_search`, `file_upload`, `file_download` | Local-disk file ingest |
| `register_computer`, `connection_info`, `ping`, `get_status` | Device and transport health |
| `sibling_claim`, `sibling_complete`, `sibling_pending`, `sibling_reject` | Sibling dispatch runs on the owner's local Claude Code |
| `cortex_sub_agents`, `cortex_set_sub_agent_tier`, `cortex_sub_agent_performance` | Sub-agent tier registry, local |
| `send_message`, `read_responses` | Device messaging |
| `get_context` | Overlaps `cortex_recent`, but includes the ACTIVE local session |
| `audit_projects`, `audit_notes`, `audit_data_quality`, `weekly_review` | Owner-facing maintenance over the local handle |

## Keep, and deliberately never port: People

`cortex_people_*` (11 tools) stay desktop-only and owner-only. People is
intentionally absent from the cloud MCP surface: contacts and `person_notes`
are third-party personal data, and exposing them to external connectors is a
different privacy decision from exposing the owner's own notes. The cloud
made notes connector-visible on 2026-08-04 with an env-var clamp; People got
no such change and should not inherit one by accident.

## Sequencing

1. Merge and verify the cloud notes retrieval and `cortex_chat` work live.
2. Remove the retired tools from `cortex_mcp/server.py` in one commit, so
   the diff reads as a deletion rather than a refactor.
3. Leave the keep list alone until the Agent plan absorbs local capture.

Do not remove anything before step 1 confirms the cloud replacement answers
correctly against the real corpus. A tool that exists and is redundant costs
context; a capability that vanishes before its replacement is proven costs
the owner their memory.
