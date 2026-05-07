# Cortex Desktop v0.18.0 (cycle complete — 2026-05-06)

**Status:** Pre-release. Latest dev tag: `v0.18.0-dev.14`.
**Theme:** Plugin sidecar architecture + cortex-vision integration end-to-end.

This file is the cycle-level changelog. Once v0.18.0 stable cuts, it
becomes the official release notes.

---

## Headline

v0.18 introduces the **plugin sidecar architecture** — Cortex Hub
can now host first-class third-party plugins that run as separate
processes, are installed via GitHub releases, and proxy through the
Hub. The cortex-vision plugin (video understanding: batch, journal,
live OBS) ships end-to-end as the first user of this architecture.

The cycle landed in 13 dev releases between 2026-05-05 and
2026-05-06, organized around the cortex-vision team's 6-phase
roadmap. Plus several plumbing fixes the cycle surfaced and a polish
pass on cross-source labeling at the end.

---

## What ships

### Plugin sidecar harness (Phase 0)

- `services/plugin_manager.py` — registry persistence at
  `%APPDATA%/Cortex/plugins/registry.json`, threaded subprocess
  lifecycle, 5-second asyncio health loop, dev-mode auto-detect,
  log capture per plugin
- `routers/video.py` — pure HTTP proxy with structured 503/502/504
  errors, hop-by-hop header stripping (incl. date/server to avoid
  uvicorn double-stamp), streaming-client lifetime tied to response
  generator. Phase 4 added a sibling WebSocket pass-through handler.
- `routers/plugins.py` — admin API (8 endpoints): list, marketplace,
  install, update, uninstall, restart, health, check-updates,
  plus `dev-register` for agent-driven dev-mode registration
- `components/settings/PluginsTab.tsx` — Plugins card in Settings
  with installed list (status dots, version, restart, uninstall),
  marketplace list (Install, Register dev sidecar), update
  detection (badge + "Update to vX.Y.Z" button), and a Configure
  form for cortex-vision specifically
- App.tsx + Layout.tsx — `'video'` reserved in the Page union; nav
  item gated on `cortex-vision.is_running`; deep-link guard
  bounces missing-plugin navigations to Settings
- `cortex-desktop/tests/test_plugin_manager.py` — 27 tests passing
  in 5s including registry CRUD, spawn/stop, the dies-flips-red
  lifecycle case, sandbox/path safety, install/update/uninstall,
  SHA256 verification, retry-on-locked-handle, install_locked
  rollback

### Cortex Vision plugin integration (Phases 1, 3, 4)

All four cortex-vision modes wired through the Hub:

- **FileMode** (Phase 1) — paste a YouTube/TikTok URL, get scenes +
  descriptions + narrative. Polls `/api/video/sessions/{id}` every
  2s. Built on `lib/videoApi.ts` + `useVideoJob` hook.
- **JournalMode** (Phase 3) — `getDisplayMedia` + `getUserMedia` +
  MediaRecorder (vp9+opus with fallback chain) → multipart upload
  to `/api/video/jobs/upload` → same polling pipeline as FileMode.
  Refresh-resilient.
- **LiveMode** (Phase 4) — camera picker (with OBS-default
  heuristic), audio source picker, transcribe-audio toggle, audio
  level meter (RMS+peak), live scene grid driven by WebSocket
  events.
- **History** — cross-mode session list backed by
  `GET /api/video/sessions`.

Plus `SessionStatusView.tsx` (shared scene grid + narrative
display, used by both FileMode and JournalMode).

### Real install + Configure (Phase 5)

- `PluginManager.install` does the full GitHub-release flow:
  fetch latest (or specific tag) → find asset + .sha256 sibling →
  stream-download to `%TEMP%` → verify SHA256 → stop existing
  process → backup-existing-install-dir → extract → locate exe →
  upsert registry → start. Atomic backup-and-rollback if
  extraction fails midway. Spawn failures (port conflict, etc.)
  are LOGGED but do NOT roll back — install considered successful.
- `PluginManager.update` reuses the install code path with
  `version="latest"`.
- `PluginManager.uninstall` stops process, optionally rmtrees
  install_dir (gated on `keep_user_data=False` AND a defensive
  path check that the dir is under our managed plugins root),
  then removes registry entry — flipping registry first would
  leave the user in the bad state where registry says clean but
  disk says dirty.
- `PluginManager.check_updates` populates per-plugin
  `latest_available_version` + `last_update_check_at` fields.
  Once-per-launch fired in main.py startup hook + manual button.
- `CortexVisionConfigForm.tsx` — describer + transcribe sections
  with URL/Model/API key fields, per-section Test connection (auto-
  populates Model dropdown from upstream's available_models), Save.
  No restart needed — sidecar reads config on every request.

### Audio capture in LiveMode (Phase 4 expansion)

cortex-vision v0.4.0 added audio. Frontend wired:

- `GET /api/video/live/audio-devices` for the picker
- `audio_source: int | str | null` on `liveStart` (null = video-only,
  "desktop" = WASAPI loopback, int = sounddevice index)
- `transcribe_audio: bool` toggle
- New WS event types in `LiveEvent` union: `audio_level` (RMS+peak,
  ~10Hz), `transcribing` (post-Stop while whisper runs),
  `transcribed` (segment_count + scenes_with_audio),
  `transcribe_skipped`, `transcribe_failed`
- Live audio meter component (color-coded green/amber/red, peak
  indicator) under the stats grid

### Hardening + polish

- **dev.7**: LiveMode fixed — was auto-attaching to phantom
  sessions on mount; now requires explicit Start, calls liveStop
  on unmount when the session was started by this component.
  Camera picker uses native_resolution/native_fps fields with
  OBS-name + highest-resolution heuristic. Errors render in a
  prominent banner.
- **dev.10**: install/uninstall hardened against WinError 5
  ("file in use"). New helpers: `_force_release_install_dir`
  (PowerShell-driven kill anything holding the port or path),
  `_rename_with_retry` and `_rmtree_with_retry` (5×200ms retry
  loops). New `InstallLocked` exception → 409 with structured
  recovery message. Pre-cleans stale `.bak` dirs from prior
  failed installs. Belt-and-braces sanity check in uninstall
  that install_dir is actually gone before flipping registry.
- **dev.11**: auto-respawn on crash (`restart_on_crash: bool`,
  capped at 5 attempts with linear backoff, counter resets on
  successful health), update detection cadence (cached
  `latest_available_version` per plugin, populated by startup hook
  and manual "Check for updates" button, "Update to vX.Y.Z" green
  button on the row when available).
- **dev.13**: cross-source labeling on the Overseer imports panel.
  Renamed "Imported Claude Sessions" → "Imported AI Conversations"
  after the historical ChatGPT bulk import made the old title a
  lie. New `SourceBadge` component renders a compact colored pill
  per row: orange "CC" for claude-code, green "GPT" for chatgpt,
  gray for any future source.

---

## Versioning across the cycle

| Tag | Date | Theme |
|---|---|---|
| v0.18.0-dev.1 | 2026-05-05 | Phase 0 plugin sidecar harness |
| v0.18.0-dev.2 | 2026-05-05 | dev-register endpoint (UWP sandbox workaround) |
| v0.18.0-dev.3 | 2026-05-05 | Phase 1 FileMode |
| v0.18.0-dev.4 | 2026-05-05 | Phase 3 JournalMode |
| v0.18.0-dev.5 | 2026-05-05 | Phase 4 LiveMode + WS bridge |
| v0.18.0-dev.6 | 2026-05-05 | Phase 2/6 overseer bridge + transcribe toggle |
| v0.18.0-dev.7 | 2026-05-05 | LiveMode picker + lifecycle fixes |
| v0.18.0-dev.8 | 2026-05-05 | Phase 5 real install/update from GitHub releases |
| v0.18.0-dev.9 | 2026-05-05 | Configure form for cortex-vision |
| v0.18.0-dev.10 | 2026-05-05 | install/uninstall hardening (WinError 5) |
| v0.18.0-dev.11 | 2026-05-05 | auto-respawn + update detection cadence |
| v0.18.0-dev.12 | 2026-05-06 | Live mode audio capture (cortex-vision v0.4.0 contract) |
| v0.18.0-dev.13 | 2026-05-06 | Cross-source labeling on imports panel |
| v0.18.0-dev.20 | 2026-05-07 | Slice 8 Phase 2: overseer chat file uploads |

### dev.20 — Overseer chat file uploads (text + image + pdf)

The overseer chat surface accepts attachments. Multipart upload at
the Hub (`POST /api/overseer/chat/upload`, max 10 files, 5MB each,
type-allowlisted) forwards each file to the Pi via the existing
`/files/uploads` endpoint and returns refs that the frontend then
submits with the next chat call. The Pi reads bytes off disk and
either inlines text/pdf into the user prompt or builds an
OpenAI-compat multimodal content block for images. New
`chat_message_files` table on `overseer.db` keeps attachment refs
FK'd to the user turn so chat history re-renders badges after a
reload.

UX: paperclip button next to the textarea, drag-drop anywhere over
the composer area, pending chips above the input (image thumbs /
TXT/PDF/FILE badges) with × to remove, auto-scroll-to-bottom that
respects manual scroll-up, "Add a question, or send the files
alone…" hint when files are queued. Send blocks while uploads are
in flight.

Pairs with cortex-core commit `f34c3cd` (Slice 8 Phase 2 backend).

Verification (this dev tag pre-soak):
* curl text → Opus 4.7 quoted the exact unique token from the file
* curl 256×256 PNG → Opus identified the precise hex `#FF8C00`
* browser end-to-end: paperclip → upload → chat → reply rendered
  with attachment badge in the bubble; text-only and image both
  observed working

Streaming (Slice C / dev.21) and chat polish (regenerate / continue
/ syntax highlighting — Slice D / dev.22) still queued.

---

## Off-cycle work the v0.18 cycle surfaced

These shipped during the cycle but aren't bound to it:

### Slice 5.6.1 — temporal cadence catchup (cortex-core repo)

Diagnosed during 2026-05-06 night triage: Tory reported the daily
narrative didn't trigger at 10pm. Investigation revealed the
auto-trigger had **never** fired since Slice 5 deployed 3 days
earlier — every existing temporal_narratives row was
`triggered_by='manual'`.

Two compounding bugs:
- Step 0 was gated on `not budget.exhausted()`, negating Slice
  5.6's bypass-budget design (the bypass lives INSIDE
  `_run_temporal_cadence` but the outer guard short-circuited
  before it ran)
- Trigger window was just 22:00-23:59 local with no catchup —
  any miss (Pi reboot, AV blip, transient bug) meant the period
  could never auto-generate

Fix: drop the budget guard from Step 0; replace single-period
logic with enumerate-with-catchup that walks back 7 days for
daily, 5 weeks for weekly, 4 months for monthly, 2 years for
yearly. The dedup gate handles "already generated" so catchup
is a no-op when caught up.

Cortex-core commit `3a1eb46` (local, not pushed). Verified live —
4 auto-fired narratives in immediate aftermath, the first ever.

See `~/.claude/projects/.../memory/slice_5_6_1_temporal_catchup.md`.

### Historical data import (cortex.db + overseer.db)

Bridged Tory's pre-Cortex life data from
`C:\Users\User\Local History AI\db\tory_life.db` (Apr 2023 → Mar 2026).

| Phase | What | Cost |
|---|---|---|
| 1 (peer records) | +70 projects, +16 people, +6 inventions, 13 orgs reconciled | $0 |
| 2 (yearly retros) | 3 yearly narratives 2023/2024/2025 via Sonnet | $0.06 |
| 3 (ChatGPT) | 1,728 conversations imported as `imported_sessions` source='chatgpt' | $0 |
| 3b (bulk gist) | 1,725 individual gists via Sonnet (parallel 6-worker, 12 min total) | $9.83 |

**Total LLM spend: $9.89** (under the $17 budget).

Cortex now has memory back to **December 2013** (earliest Amazon
purchase in tory_life.db) for any future yearly retrospectives.

Backups at `C:\dev\ttx\Cortex\backups\2026-05-06_post-chatgpt-bulk-summarize\`
(96 MB: cortex.db + overseer.db + tory_life.db + 1,728 source
JSONL files + portable CSV/JSON dumps + README with restore
instructions).

See `~/.claude/projects/.../memory/historical_data_import_complete.md`.

### Memory + repos updated

- `feedback_uwp_appdata_sandbox_redirect.md` — captured during dev.1
  testing when agent edits to `%APPDATA%\Cortex\plugins\registry.json`
  silently went to a UWP sandbox instead of the real path. The fix
  was the dev-register endpoint (dev.2).
- Memory index `MEMORY.md` updated with pointers to all the new
  feedback + complete files.

---

## Migration notes

After installing v0.18:

- **Schema migrations are automatic**. New tables (plugin registry,
  no DB changes — registry.json is JSON in `%APPDATA%`).
- **First run** — Plugins card in Settings. cortex-vision marketplace
  entry visible. Click Install for one-click bundle install +
  spawn (~30-60s). Click Configure to enter LM Studio URL.
- **Existing dev-mode entries** carry over from `registry.json` if
  you had the plugin running locally during the cycle.
- **Update detection** — Hub fires `check_updates` once on startup;
  manual "Check for updates" button in Plugins card top-right.
  When a new cortex-vision lands, you'll see "↑ vX.Y.Z" badge plus
  green "Update to vX.Y.Z" button on the cortex-vision row.

---

## dev.14 — Voice transcription crash fix (off-cycle, 2026-05-06)

Voice journal stopped working on Tory's i7-14700F roughly halfway
through 2026-05-06: every `whisper-cli.exe` invocation died ~4 seconds
after launch with no output. Initial reports pointed at the recent
NVIDIA driver / Vulkan loader, but the crash also reproduced under
`-ng` (CPU only), ruling out the GPU path. Windows Event Viewer
showed exit `0xC000001D` (`STATUS_ILLEGAL_INSTRUCTION`) at a fixed
offset inside `whisper-cli.exe` itself.

**Root cause.** `scripts/build_whisper_cpp.py` configured CMake with
no ISA constraints, so ggml's default `GGML_NATIVE=ON` baked the
build host's `-march=native` into the binary. The GitHub Actions
Windows runner pool includes Xeon Platinum SKUs with full AVX-512;
binaries built on those land in CI artifacts with ~6900 EVEX-prefixed
(AVX-512) instructions. None of those execute on Intel hybrid CPUs
(12th-gen+ Alder/Raptor Lake disabled AVX-512 entirely because the
E-cores lack it) or on most consumer Ryzen desktop parts.

The original `dev.13` binary had ~6919 EVEX-prefix opcodes and
crashed on the user's i7-14700F (no AVX-512). Confirmed via the
Win32 `IsProcessorFeaturePresent` API + EVEX-prefix scan over the
binary's `.text`.

**Fix.**

- `scripts/build_whisper_cpp.py` now passes
  `-DGGML_NATIVE=OFF -DGGML_AVX2=ON -DGGML_FMA=ON -DGGML_F16C=ON
  -DGGML_AVX512=OFF` (plus the `_VBMI`/`_VNNI`/`_BF16` variants).
  Resulting binary requires only AVX2 + FMA + F16C, available on
  every x86_64 CPU since Haswell (2013). Marker file gains a
  `+avx2` suffix so the UI/log can confirm the baseline.
- `routers/transcribe.py` gains a defensive runtime layer:
  - `_classify_exit_code()` recognises the three native-fault DWORDs
    (`0xC000001D`, `0xC0000409`, `0xC0000005`) and Python's signed
    representation of them.
  - On a hard crash on the GPU path, the background runner retries
    once with `-ng`. If the CPU run succeeds, `force_cpu` becomes
    sticky for the rest of the Hub process.
  - When *both* paths crash natively, the user sees a clear
    remediation message ("update to v0.18.0-dev.14 or later — that
    release ships an AVX2-baseline binary") instead of stderr soup.
  - `whisper_force_cpu` config flag persists the GPU-bypass choice
    across restarts.
- `routers/settings.py` env_map gains `whisper_model →
  CORTEX_HUB_WHISPER_MODEL` and `whisper_force_cpu →
  CORTEX_HUB_WHISPER_FORCE_CPU`; `SettingsUpdate` and `DEFAULT_CONFIG`
  carry both. Previously `whisper_model` couldn't actually be saved
  through the settings UI.
- `JournalTab.tsx` reads the active model name + size from
  `/api/transcribe/status` instead of hardcoding "large-v3" / "~3GB"
  in the download placeholder. Adds a sticky amber banner that
  surfaces native-fault signatures with remediation copy. Tested
  against `dev.14` build output: `npm run build` clean, `tsc -b`
  clean.

**Diagnosis path that didn't pan out (documented for future
reference).** The original three hypotheses — Vulkan loader/driver
mismatch, binary corruption, missing C++ runtime DLL — were all
ruled out by the `-ng` reproduction and the Event Viewer "faulting
module: whisper-cli.exe" line. A 4th hypothesis (build-host ISA
over-specialisation) matched the evidence and was confirmed by the
EVEX-prefix scan plus the `IsProcessorFeaturePresent(41)` = `False`
check on the user's CPU.

**Operator note.** Existing dev.13 installs need to either update
to dev.14 (CI builds an AVX2-baseline binary now, regardless of
which runner picks up the job) or replace the bundled
`whisper-cli.exe` with one built locally from the patched
`build_whisper_cpp.py`. The runtime crash-detection layer doesn't
rescue dev.13 by itself — the second `-ng` retry hits the same
illegal instruction inside ggml's CPU kernels.

---

## In flight (deferred to v0.19)

- **Phase 6 polish (cortex-vision side)** — settings page additions
  (describer/audio/thresholds), describer hot-swap, resume past
  session, CLI mode, auto-cleanup, remote sidecar UI. Most of these
  are cortex-vision-side; cortex-desktop just exposes whatever the
  plugin manifest declares.
- **Project narrative gain people block** — pairs with Slice 6 CP3
- **Slice 4 CP3** — per-project actions (rename / archive / set
  focus / merge / inline classify); absorbs the standalone Classify tab
- **Slice 6 CP2** — Hub UI Network section in Journal tab (held
  until ~30+ people accumulate from agent capture)

---

## Out-of-scope queued (future cycles)

- **MCP tool surface in cortex-mcp** so Claude Code agents in other
  repos can drive cortex-vision (process video, search past sessions)
- **WebSocket pass-through perf tuning** — currently ~10Hz audio_level
  events through the bridge; could backpressure if ever streaming
  larger payloads
- **Plugin marketplace JSON hosted on GitHub Pages** — currently
  the marketplace list is hardcoded in `plugin_manager.py` (just
  cortex-vision). Phase 5+ would fetch from a registry.
