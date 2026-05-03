# Cortex Desktop v0.16.0

**Released:** May 2026
**Theme:** Project-Centric Overseer + UI re-organization

The 0.16 cycle finishes the Overseer plugin (Slice 3 wrap + Slice 4) and reorganizes the Hub around the projects the user actually works on. Most code lives across two repos:

- **cortex-desktop** (this repo) — Hub UI + backend proxies
- **[cortex-core](https://github.com/turfptax/cortex-core)** — Pi-side overseer plugin, schema, narrative + rollup engine

---

## Highlights

### Slice 4 — Project-Centric Overseer (NEW)

The Overseer now produces per-project intelligence on top of the existing memory layer.

- **Rollup data layer** (CP1a) — every imported Claude Code session gets parsed for token usage, models used, and tool-use file paths. Aggregates per project: `active_minutes_total`, `cost_usd_estimate` (with `cost_known_complete` flag), top files (post-exclusion), models-used breakdown, lifespan, days_active_30/90.
- **LLM narrative** (CP1b) — Sonnet writes a 3-paragraph rollup per project: what it's about, what's been happening recently, patterns or drift worth flagging. Includes an "Open questions still live" section when applicable. Loop fires once per 24h per project + ≥3 new sessions trigger; daily-budget capped.
- **Projects tab UI** (CP2) — Overseer → Projects sub-tab. One card per project sorted by Active hours desc. Hero Active-hours metric, narrative front-and-center with "Read full ▾" toggle, collapsible Timeline + Compute panels, per-card "Regenerate narrative" button.
- **Activity-shape callouts** — `⚡ mostly quick queries` (UFOSINT pattern), `🌒 dormant spike`, `○ dormant` — shapes are readable at a glance.
- **Active-only / All toggle** — hide dormant projects when you just want to see what you're working on now.
- **Backfill script** — `cortex-core/scripts/backfill_session_stats.py` (one-shot, idempotent, `--force` re-parses). Run it once after pulling 0.16 to populate stats on existing imports.

### UI reorganization

- **Sidebar trimmed from 7 to 5 tabs** — Chat / Pi / Data / Overseer / Settings. Training and Games are now Pi inner tabs (they're Pi-attached features).
- **Pi page consolidation** — 8 inner tabs → 7. The new "System" tab rolls up the old Status + Firmware tabs into one section. Tab strip is `overflow-x-auto whitespace-nowrap` so future additions don't break layout.

### Data Explorer (Polish slice + 0.16 fixes)

- Force-directed graph with d3-force, react-flow canvas, floating perimeter edges (extracted to `lib/graphengine/`).
- Wheel-zoom (panOnScroll=false) + liquid layout (alphaDecay 0.012 + position carry across filter changes) + 200ms ease-out-quint focus transitions.
- **Smart empty-state** — distinguishes "no graph data" from "filters hide everything" with the active filter list + Reset button.
- **All projects now visible** (0.16 fix) — projects were previously hidden by `hide-disconnected` when they lacked question-evidence edges. Now exempt; 47 projects render.
- **Focus question is a real filter** (0.16 fix) — pinning a question via the dropdown hides everything outside its 2-hop neighborhood (was: dim only). Click-focus on a node still uses the dim treatment for ad-hoc poking.
- Project node sizes now scale with active hours; dormant projects fade.

### Chat context cleanup

The overseer's chat system prompt got a real diet:
- In-system transcript capped to 12 turns × 800 chars (was unbounded → ~10K chars on chatty conversations)
- Journal trimmed: 4 × 350 → 3 × 250
- Future-notes trimmed: 2 × 500 → 1 × 400
- Rollups trimmed: 3 × 160 → 2 × 120 (anomalies pass)

Net: typical chat context dropped from ~10K chars to ~5–6K chars.

### Project scan UI refresh

- Per-project grouping with NEW / ON-PI badges (was a 435-row flat scroll for users with many sessions)
- Whole group-header row clickable; chevron rotates via CSS transform
- Pill-shaped badges with color hierarchy
- Inner rows indented `pl-9` so groups read as a clean nested list

### Bell digestibility

- Notifications grouped by `rule_name` so 30 instances of the same rule render as one expandable card
- Auto-archive after 60d
- Auto-resolve when the rule no longer fires
- Snooze + Archive + Touch actions (slice 3i CP1)

---

## Other notable additions across the cycle

- **Slice 3i CP2 — Correction-feedback loop**: chat detector ("you're wrong / actually no / correction:"), dialectic resolutions, manual entries flow into `interpretation_corrections`. Sonnet `distill_corrections.py` clusters them into blindspot proposals; loop fires once per 24h when ≥3 uncondidated corrections exist.
- **Branching scheme** established for multi-agent work: `master` = released/tagged, `feat/<thing>` per agent.

---

## Migration notes

After installing 0.16:

1. **Run the Slice 4 backfill once** (Pi-side):
   ```bash
   ssh turfptax@10.0.0.25
   cd ~/cortex-core
   sudo OVERSEER_DB=/home/turfptax/cortex-core/plugins/overseer/data/overseer.db \
     python3 scripts/backfill_session_stats.py
   ```
   ~1s per session × ~500 sessions = under a minute. Idempotent — re-running skips already-processed rows.

2. **Schema migrations are automatic** on cortex-core boot:
   - `_migrate_4_cp1b` adds `active_minutes_total`, `avg_active_minutes_per_session`, `median_active_minutes_per_session`, `narrative_cost_usd` to `project_summaries`
   - All migrations in the chain are idempotent on every boot

3. **First Overseer → Projects tab open** auto-fetches and shows ~47 cards with stats. Narratives populate over the next ~16 ticks (loop schedules 3 projects per tick) OR you can click "Regenerate narrative" per-card to get one immediately.

---

## Versioning across the cycle

- v0.16.0-dev.1  — Polish CP2 Hub: Bell grouping + cohesion sweep
- v0.16.0-dev.2  — Polish CP1 Hub: Data Explorer foundation
- v0.16.0-dev.3  — Polish CP2 Hub: Explorer interactivity
- v0.16.0-dev.4  — Polish CP2: liquid layout + wheel zoom
- v0.16.0-dev.5  — Polish CP3 Hub: scan UI grouped by project + handleScan flag fix
- v0.16.0-dev.6  — Polish CP3 build fix
- v0.16.0-dev.7  — Publish merged sidebar + viz-engine work
- v0.16.0-dev.8  — PiPage tab cleanup (8 → 7 tabs + scrollable strip)
- v0.16.0-dev.9  — Finishing Pass CP1: Explorer empty-state + focus polish
- v0.16.0-dev.10 — Polish closeout: focus 200ms + chat trim + scan UI refinement
- v0.16.0-dev.11 — Slice 4 CP2: Projects tab UI
- v0.16.0-dev.12 — CP2 polish: hero metric + truncate + icons + dormant fade + Active-only
- v0.16.0-dev.13 — Explorer fixes: show all projects, real question filter, dormant fade
- **v0.16.0**     — official release (rollup of all of the above)

cortex-core companion commits live in [cortex-core@master](https://github.com/turfptax/cortex-core/commits/master) — search for "Slice 4" / "Slice 3i" / "Polish" in the log.
