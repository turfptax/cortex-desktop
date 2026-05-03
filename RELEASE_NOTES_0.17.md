# Cortex Desktop v0.17.0 (cycle in progress)

**Status:** Pre-release. Latest dev tag: `v0.17.0-dev.2`.
**Theme:** Temporal Cadence + Human Journal — the Overseer's heartbeat.

This file is updated as the cycle progresses. Once v0.17.0 stable
cuts, this becomes the official release notes for the cycle.

---

## Locked principle (added at the top of every Slice 5 prompt)

> The Overseer should remain a quiet, lightweight memory layer. It
> does three things well: capture, surface, and connect. It does
> NOT become a full journaling app or life coach.

If a future feature proposal would push the Overseer toward
streak-tracking, nag-notifications, suggested-actions, or "today's
goal" surfaces — that proposal violates the principle and should
be declined.

---

## Highlights

### Slice 5 — Temporal Cadence + Human Journal (NEW)

The Overseer's first time-anchored layer. Three Sonnet narratives
on a local-time schedule, plus a textarea for the user to write
what's on their mind.

- **Daily snapshot** — fires 22:00 local. "What moved today?"
  Today-specific session/cost/file numbers (computed from
  `imported_sessions` in the window, NOT lifetime aggregates).
  Ends with one sentence connecting to a standing
  question/theme/pattern when one genuinely pulls.
- **Weekly synthesis** — fires 22:00 Sunday. Summarizes the 7
  daily snapshots; flags cross-project connections; notes
  active vs stalled projects.
- **Monthly review** — fires 22:00 on the 1st. Light reflection;
  skipped if no daily snapshot in the past 14 days (the
  "user disengaged" gate).
- All three persist to the new `temporal_narratives` table.
  `UNIQUE(kind, period_label)` prevents double-generation
  across loop ticks within the same trigger window.
- One narrative kind per tick max; bounded by the existing
  daily LLM budget.

**Per-call cost** (Sonnet, real measurements): Daily ~$0.01,
Weekly ~$0.02, Monthly ~$0.01.

### Human journal

- New `human_journal_entries` table — free-form, multiple per day allowed.
- Cmd/Ctrl+Enter saves an entry from the textarea in the Journal tab.
- Period entries auto-included in temporal narrative prompts ("here's
  what the user wrote today / this week / this month").

### Journal tab restructure

The Journal tab is now the single home for everything reflective in
the Hub. Three stacked sections, top → bottom:

1. **Your journal** — textarea + recent entries (delete on hover)
2. **Temporal narratives** — Daily / Weekly / Monthly cards with
   the latest of each kind shown by default; "All <kind>"
   expands; per-card "Generate now" button bypasses the 22:00-
   local trigger
3. **Overseer reflections** — the original tick-based first-person
   journal moved verbatim to the bottom

---

## Migration notes

After installing v0.17:

- **No backfill needed**. Schema migrations are automatic on
  cortex-core boot. `_migrate_5_cadence` is a no-op today
  (`CREATE TABLE IF NOT EXISTS` handles fresh + already-migrated
  DBs) but stays in the chain as the future hook.
- The first daily snapshot will appear at the next 22:00 local
  after install. To see one immediately, hit "Generate now" in
  the Journal tab Daily card.

---

## Versioning across the cycle

| Tag | Date | What |
|---|---|---|
| v0.17.0-dev.1 | 2026-05-03 | Slice 5 CP1+CP2 backend (cadence + human-journal — backend only, no UI) |
| v0.17.0-dev.2 | 2026-05-03 | Slice 5 CP3+CP4 (Journal tab UI restructure) |

Companion cortex-core commits live at
[cortex-core@cfa7ac0](https://github.com/turfptax/cortex-core/commit/cfa7ac0) — see commit body for the full module/route inventory.

---

## In flight (deferred until soak ends)

- **CP6 polish** — "Today's context" line on Daily ("3 sessions ·
  2.4 active hours · Cortex dominant"), hover-for-exact-time on
  human entries, search/filter on human entries by date range
- **Slice 4 CP3** — per-project rename / archive / set focus /
  merge / inline classify; absorbs the standalone Classify tab

## Not in this cycle (queued for future)

- **Possible Slice 6** — MCP integration of Overseer memory so
  Claude Code agents in other repos can both READ and CONTRIBUTE
  to the memory layer. Biggest leverage option on the table.
- **Journal annotations** — the deferred idea where the Overseer
  retroactively annotates its own past journal entries
  ("aged well", "I misunderstood", "this loop I later broke")
