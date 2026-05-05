# Cortex Desktop v0.17.0 (cycle in progress)

**Status:** Pre-release. Latest dev tag: `v0.17.0-dev.13`.
**Theme:** Temporal cadence + relationships + voice — three new
axes for the memory layer; engineering correctness fixes
underneath.

This file is updated as the cycle progresses. Once v0.17.0 stable
cuts, this becomes the official release notes for the cycle.

---

## Locked principle (carried forward from Slice 5)

> The Overseer should remain a quiet, lightweight memory layer.
> It does three things well: **capture, surface, and connect.** It
> does NOT become a full journaling app or life coach.

This principle now lives in three load-bearing places:
1. Top of every Slice 5 Sonnet prompt (`SHARED_PRINCIPLE` constant)
2. `slice_5_complete.md` memory file (with the "decline future
   proposals that violate it" framing)
3. This file + cortex-desktop's `CLAUDE.md` Overseer section

Slice 6 (People) and Slice 7 (Voice) both ship under this
principle — they're capture/surface/connect mechanisms, not
streaks/nudges/coaching.

---

## Highlights

### Slice 5 — Temporal Cadence + Human Journal

The Overseer's first time-anchored layer. Four Sonnet narratives
on a local-time schedule, plus a textarea for the user to write
what's on their mind.

- **Daily** at 22:00 local — "what moved today?"
- **Weekly** Sunday 22:00 local — synthesizes 7 dailies + cross-project signals
- **Monthly** 1st of month 22:00 local — synthesizes weeklies + open-question lifecycle (skip-gate: no daily in past 14 days)
- **Yearly** Jan 1 22:00 local — synthesizes the year's monthlies (skip-gate: ≥1 monthly in the year being reviewed)

All four BYPASS the daily LLM budget (Slice 5.6) — they're
once-per-period and missing the trigger window is permanent.
Per-call cost cap ($0.05) still applies; cost still logged for
audit. ~$5/year total annual spend across all temporal
narratives.

`temporal_narratives` table with `UNIQUE(kind, period_label)`
prevents double-generation across loop ticks within a trigger
window.

### Slice 5.5 — Cadence calibration (post-soak fix)

After Tory's first soak day, temporal cadence wasn't auto-
firing. Diagnosed: budget exhausted before 22:00 CDT trigger
because (a) the UTC budget reset landed at 19:00 CDT and
(b) the journal step was eating 70-80% of the daily budget.

Four fixes:
1. Tick interval 5min → 15min (288 ticks/day → 96)
2. Journal capped at 6 entries / local-day, 90-min cooldown
3. Temporal cadence Step 9 → Step 0 (runs FIRST)
4. DailyBudget reset switched UTC → local-day

### Human journal

- New `human_journal_entries` table — free-form, multiple per day allowed
- Cmd/Ctrl+Enter saves an entry from the textarea
- Period entries auto-included in temporal narrative prompts
- Voice transcription input in CP4+ (see Slice 7 below)

### Journal tab restructure

The Journal tab is now the single home for everything reflective:

1. **Your journal** — textarea + recent entries + 🎤 voice button
2. **Temporal narratives** — Daily / Weekly / Monthly cards (Yearly will appear when Jan 1 fires); per-card "Generate now"
3. **Overseer reflections** — the original tick-based first-person journal

### Slice 6 — People as first-class memory entity

The relationship layer. Captures who matters in the user's work
+ how they connect to projects.

- `overseer_people` + `overseer_project_people` tables (namespaced
  to avoid collision with CortexDB's simpler `people` table)
- Audit trail (`created_by_agent`, `created_by_session_id`) on
  every row for spot-checking what's been captured
- **9 MCP tools** (`cortex_people_*`) are the PRIMARY entry
  surface. Agents working with Tory in his other repos call
  these to capture relationships during work. Tool descriptions
  opinionated about WHEN to use (recurring collaborators,
  subjects, mentors) vs WHEN NOT (casual mentions, fictional
  names, code variables, AI agents).
- `cortex_people_stats` for cross-cutting counts: orphans (no
  project links), multi-project connectors, top expertise tags,
  recent additions with audit info.

Hub UI for review/curation (CP2) deferred until ~30+ people
accumulate. Narrative integration (CP3) deferred until Slice 5
soak completes.

### Slice 7 — Voice journal entries via local Whisper

Tory often records voice memos / videos for journal entries.
Slice 7 lets him upload them through the Hub and get a
transcript that pre-fills the journal textarea.

- **whisper.cpp bundled in installer** — single ~5MB native
  binary, no Python deps for transcription. Works on the
  installed exe path out of the box (the dev.7 openai-whisper
  approach broke for users on the bundled exe; dev.9 fixed it
  architecturally).
- **Default model: `large-v3`** (~3GB GGML file). Auto-downloads
  from HuggingFace on first transcription; cached locally
  forever. Single network call ever related to voice; after
  that, fully offline.
- **Vulkan GPU support** in the bundled binary (CI installs
  Vulkan SDK before whisper.cpp build). 5-10× speedup on any
  modern GPU (NVIDIA / AMD / Intel via vulkan-1.dll).
- **All CPU threads** used (`whisper-cli -t <cpu_count>`,
  was capped at 4) — meaningful CPU speedup as fallback.
- **Async with live progress (CP4):** POST returns 202
  immediately, frontend polls status every 1.5s. Progress
  percentage parsed from whisper-cli's stderr surfaces in the
  textarea placeholder + 🎤 button.
- **Refresh resilient (CP4):** browser reload during
  transcription doesn't lose anything. Server-side subprocess
  + state outlive the request.

Privacy: file never leaves the host. ffmpeg normalizes audio
+ extracts audio from video.

---

## Migration notes

After installing v0.17:

- **Schema migrations are automatic** on cortex-core boot.
  `_migrate_5_cadence` + `_migrate_6_people` are no-ops today
  because `CREATE TABLE IF NOT EXISTS` handles fresh + existing
  installs. Hooks kept as chain anchors for future column adds.
- **Voice transcription** prompts a one-time ~3GB model download
  on first 🎤 click. UI shows progress.
- **Floodgates for People MCP tools:** when starting a Claude
  Code session in another repo, drop this instruction:

  > Use the cortex_people_* tools when you encounter named
  > people who matter to my work. Always cortex_people_search
  > FIRST to avoid duplicates, then cortex_people_add or
  > cortex_people_update.

---

## Versioning across the cycle

| Tag | Date | What |
|---|---|---|
| v0.17.0-dev.1 | 2026-05-03 | Slice 5 CP1+CP2 backend (cadence + human-journal) |
| v0.17.0-dev.2 | 2026-05-03 | Slice 5 CP3+CP4 (Journal tab UI restructure) |
| v0.17.0-dev.3 | 2026-05-04 | Slice 6 CP1: People schema + MCP tools |
| v0.17.0-dev.4 | 2026-05-04 | Slice 6 stats tool + unlink tool |
| v0.17.0-dev.5 | 2026-05-05 | Slice 5.5: cadence calibration |
| v0.17.0-dev.6 | 2026-05-05 | Slice 5.6: temporal bypass + yearly |
| v0.17.0-dev.7 | 2026-05-05 | Slice 7 CP1: openai-whisper transcribe (deprecated) |
| v0.17.0-dev.8 | 2026-05-05 | Multi-line error banner UX fix |
| v0.17.0-dev.9 | 2026-05-05 | Slice 7 CP2: switch to whisper.cpp (the architectural fix) |
| v0.17.0-dev.10 | 2026-05-05 | Stable/Dev toggle visibility fix |
| v0.17.0-dev.11 | 2026-05-05 | whisper-cli all CPU threads |
| v0.17.0-dev.12 | 2026-05-05 | Slice 7 CP3: Vulkan GPU build in CI |
| v0.17.0-dev.13 | 2026-05-05 | Slice 7 CP4: async + live progress + refresh resilience |

---

## In flight (deferred)

- **Slice 6 CP2 — Hub UI** (Network section in Journal tab)
- **Slice 6 CP3 — Narrative prompt integration** (people block)
- **Slice 7 CP5 — MCP tool** `cortex_human_journal_transcribe(file_path)` for agent-driven uploads
- **Slice 4 CP3 — per-project actions** (rename / archive / set focus / merge / inline classify); absorbs the standalone Classify tab

## Out-of-scope queued (future cycles)

- **Yearly card in Journal tab UI** — held until Jan 1 when there's actual yearly data (per Tory's call)
- **Speaker diarization** for voice transcription — different stack; probably never (single-speaker voice memos are the actual workflow)
- **Cancel-mid-flight** + multi-file queue for transcription — singleton was enough for personal-use journaling
- **Project narratives gain people block** — pairs with Slice 6 CP3
