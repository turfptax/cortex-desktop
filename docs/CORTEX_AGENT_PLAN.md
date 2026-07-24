# CORTEX AGENT: the conversion plan

Synthesized plan for the desktop team, 2026-07-24. Merges Design 1 (watcher engine + upload spine), Design 2 (tray + status UX), and Design 3 (conversion + release). Conflicts between the designs are resolved inline and marked RESOLUTION. Defects found in critique review are folded in and marked FIX where they change prior text. Hard rules: no em dashes anywhere in this document or in anything written from it; the repo forbids git add -A (stage files explicitly; scripts/video-annotator/ carries 17MB of untracked-adjacent Node artifacts); the deliverable of this plan is executed code, but this document is the spec of record.

---

## 1. Vision

Cortex is a three-tier system:

- **Cloud** (https://cortex.turfptax.com, Azure Container App from C:\dev\ttx\Cortex\Cortex-Cloud): corpus of record + web UI. Every reading, browsing, searching surface lives here.
- **Phone** (cortex-mobile): capture.
- **Desktop Agent** (this repo, C:\dev\ttx\Cortex\cortex-desktop): the local-capabilities tier. It does ONLY what the cloud fundamentally cannot:
  1. **Local AI-conversation ingest** (urgent, the corpus is starving): a watcher tailing ~/.claude/projects/**/*.jsonl plus Claude Desktop export ZIPs, pushing sessions to the cloud, uuid/hash deduped. A backlog since ~2026-07-20 drains on first run.
  2. **Local media parsing + upload**: GPU video via the cortex-vision sidecar (C:\dev\ttx\Cortex\cortex-vision), audio via bundled whisper.cpp + ffmpeg. Parse locally, upload results.
  3. **A thin tray + status UI**: connection status, counts, a drop-media-here surface, set cloud URL/token once.

**What the Agent is NOT:** a Hub. It never renders corpus content (notes, sessions, search, graphs, people, journals). It has no chat UI. The Agent process never listens on any port. Any need to SEE data is answered with a link to the cloud web app; if the cloud web app lacks a view, the view gets built there. These are hard guardrails (section 5.5), written to structurally prevent Hub regrowth.

**Future frame:** when Cortex becomes a paid single-owner MCP-as-a-service, this Agent is what a customer installs to feed their own corpus. Therefore: no hardcoded owner, config-driven cloud URL + token, plain-language status for non-technical users.

**Crown jewels kept:** the exe installer + tagged-release pipeline (.github/workflows/build-release.yml), the stable/dev auto-updater, the tray app (cortex_desktop/app.py + tray.py), whisper.cpp bundling (pinned v1.8.4, AVX2 baseline, Vulkan), the /core proxy client + %APPDATA%/Cortex config. **Shed:** hub/frontend, most hub/backend routers, the LM-Studio chat.

---

## 2. Architecture

### 2.1 Process model

One process, main-thread tray (Windows requires it; keep the app.py pattern), daemon worker threads, one shared persistent queue. No local HTTP server, ever. fastapi/uvicorn/websockets/pydantic-settings leave the dependency tree.

```
CortexAgent.exe (single process)
  main thread ......... pystray tray (cortex_desktop/tray.py, extended)
  WatcherThread ....... scans sources on an interval, enqueues UploadJobs
  UploaderThread ...... drains the queue to the cloud; owns retry/backoff/circuit breaker
  MediaBridgeThread ... polls cortex-vision for complete sessions, enqueues UploadJobs
  ServiceLoopThread ... asyncio loop hosting PluginManager startup/health (lifted from hub/backend/main.py lifespan)
  (sidecar) ........... cortex-vision.exe on :8004, spawned/health-looped by plugin_manager (unchanged; binds localhost only)

CortexAgent.exe --status-window ... separate process, tkinter window (section 5)
CortexAgent.exe --mcp ............. separate process, micro-MCP stdio (section 6.3)
```

Threads share an in-process **StatusBoard** object; the tray reads it directly. The agent also writes **%APPDATA%/Cortex/status.json** atomically (tmp + os.replace, the ingester.py mechanic) on change plus a 30s heartbeat; the status window process reads that file. RESOLUTION (Design 1 StatusBoard vs Design 2 status.json): both, one writer. StatusBoard is the in-process truth, status.json is its published snapshot and doubles as the support/liveness surface (heartbeat older than 90s = "Agent not running").

Single-instance guard: a Windows named mutex (the same mechanic the status window uses), not a lock file. FIX: the earlier O_EXCL+pid lock file has a pid-reuse race; one mechanic, used twice. Shutdown: shutdown_event to all threads, uploader finishes the in-flight request (bounded by the 110s timeout), existing 3s os._exit(0) force-timer stays as backstop.

### 2.2 Module layout

New code under cortex_local/ (ingest spine, stdlib only: urllib, threading, json, hashlib) and cortex_desktop/ (shell). Relocated survivors move out of hub/.

| Module | Responsibility |
|---|---|
| cortex_local/agent.py | start_agent(config, shutdown_event) -> StatusBoard. Constructs StatusBoard, JobQueue, CloudClient; spawns threads. Replaces the uvicorn thread slot in app.py. |
| cortex_local/cloud_client.py | Stdlib HTTP client lifted from cortex_mcp/wifi_bridge.py (_make_basic_auth_header, fresh config read per call). health(), upload_file(), import_from_path(), send_note(), list_imported_hashes() (ported from hub/backend/routers/overseer.py _already_imported_hashes, degrade to empty set on failure). Timeout 110s on all calls (the gateway /core proxy read timeout is 120s; the old 600s is dead). Typed errors: AuthError (401/403), CloudUnreachable, ServerError (5xx/429), ClientError (other 4xx). |
| cortex_local/scanner.py | scan_claude_code(): walk ~/.claude/projects/*/*.jsonl, 30-min idle mtime gate, (size, mtime) pre-filter before hashing, 64KB-chunk sha256. scan_dropzone(): consume %APPDATA%/Cortex/media_queue/ job files (no idle gate). FIX: no staging directory; job files reference original media paths, nothing is copied (section 4.1). |
| cortex_local/state.py | %APPDATA%/Cortex/local_ingest_state.json, atomic writes, schema v2 (section 2.4). |
| cortex_local/jobs.py | UploadJob dataclass + persistent JobQueue at %APPDATA%/Cortex/upload_queue.json. Idempotent enqueue on dedupe_key, next_retry_at scheduling, dead-letter state, terminal-key ledger (section 2.5). The queue surviving restarts IS the offline queue. |
| cortex_local/uploader.py | UploaderThread: circuit breaker, per-kind dispatch, StatusBoard updates. The only module that writes to the cloud. |
| cortex_local/media_bridge.py | Port of hub/backend/services/video_overseer_bridge.py; enqueues instead of pushing directly; tracks per-session per-kind outcomes (section 4.3). |
| cortex_local/status.py | StatusBoard (thread-safe counters) + status.json writer + the five-state machine (section 5.1). |
| cortex_local/logging_setup.py | RotatingFileHandler at %APPDATA%/Cortex/logs/agent.log, 5MB x 3, INFO default. Closes the no-persistent-logging gap. |
| cortex_agent/media/transcribe.py | The whisper engine lifted verbatim from hub/backend/routers/transcribe.py minus FastAPI: binary lookup chain, model auto-download (atomic .part), MODEL_ALIASES, ffmpeg normalize, _HARD_CRASH_CODES GPU-crash handling with one -ng retry + sticky force_cpu, version-marker parsing, AUDIO_EXTS/VIDEO_EXTS. |
| cortex_desktop/update_check.py | The update brain lifted from hub/backend/routers/settings.py (section 6.1 D1). |
| cortex_desktop/status_window.py | tkinter window (section 5.2). |
| Relocated as-is | services/plugin_manager.py, services/pi_client.py (ported off pydantic-settings to read cortex_desktop config directly), cortex_mcp/wifi_bridge.py + protocol.py. |

cortex_local/ingester.py retires as an entry point; its scan/hash/idle/state mechanics move into scanner.py + state.py. Its push_session_file NotImplementedError stub is deleted, not unstubbed (it targets POST /v1/imports/session-file, an endpoint that was never built).

### 2.3 Exact cloud protocol

Base URL {base} = config cloud_url, form https://\<host\>/core (the gateway /core proxy: forwards X-* headers, drops host/authorization, re-authenticates to the co-located core). Auth on every call: Authorization: Basic base64("cortex:" + cloud_token), constant-time verified gateway-side. Note (matters for first-run, section 5.3): the proxy authenticates EVERY path under /core, including /core/health; the only unauthenticated health endpoint is the gateway root /health.

**Session file push (the proven two-step from hub/backend/routers/overseer.py _import_one_path):**

```
STEP 1
POST {base}/files/uploads
Content-Type: application/octet-stream
Content-Length: <n>
X-Filename: <session_uuid>.jsonl
X-Description: Claude Code session import
X-Tags: claude-code,overseer-import
Body: raw file bytes (read fully into memory; fine for the 30MB class)
-> 200 {ok, filename, size, path, file_id}

STEP 2
POST {base}/plugins/overseer/imports/from-path
Content-Type: application/json
Body: {"path": "<path from step 1>", "source": "claude-code"}
-> 200 {imported_id | skipped | error, session_id, message_count, duration_minutes}
```

skipped ("already imported (same hash)") is SUCCESS. Only source claude-code is accepted by the core. Files over 50MB: WARN and skip in CP1; in the queue era, dead-letter after two consecutive step-1 timeouts ("file too large for proxy window"), never loop. RESOLUTION (Design 1's 50MB warn-and-attempt vs Design 3's 30MB skip): 50MB is the line, skip in CP1, attempt-then-dead-letter in CP4.

**Not used:** POST /v1/sync/push. It is row-shaped (PushIn {device, kind, rows}), OAuth scope app (the phone), whitelisted kinds, no session kind. Reserved for possible future row-kind pushes only.

**Media results:** transcripts/narratives as notes via POST {base}/api/cmd command=note (the pi_client send_note pattern), artifacts via POST {base}/files/uploads. Details in section 4. Note writes have NO server-side idempotency (a note POST simply inserts); client-side idempotency for note/artifact kinds is therefore mandatory (section 2.5).

**Dedupe bootstrap:** GET {base}/plugins/overseer/imports?source=claude-code&limit=500&offset=N, paging, collecting file_hash values.

### 2.4 Dedupe and state

Two keys: **content identity** = whole-file sha256 (decides "do I upload"), **session identity** = session uuid = the .jsonl filename stem, derived server-side from X-Filename (decides "which corpus record this replaces"). The server (Cortex-Cloud/core/plugins/overseer/__init__.py _import_one_jsonl) dedups by (source, sha256) and, for a grown file (new hash, same uuid), replaces the metadata row keeping the imported_id.

**FIX (BLOCKER, cloud-side prerequisite for CP1): grown-session re-push currently updates metadata only, it is never re-processed.** _import_one_jsonl upserts the imported_sessions row (ON CONFLICT id DO UPDATE) but never deletes the session's processed_imported_sessions row, and list_unprocessed_imported_sessions filters on that join. So the first push of a session gets gisted and every later push of appended content is invisible to the overseer tick forever. The fix is one cloud-side line in Cortex-Cloud/core/plugins/overseer/__init__.py: inside _import_one_jsonl, when replacing an existing imported_id (new hash, same uuid), delete that id's processed_imported_sessions row so the session re-enters the unprocessed set. This ships as a Cortex-Cloud change BEFORE or WITH CP1; without it the watcher under-delivers on exactly the most valuable case (active sessions that grow). With that fix in place, whole-file re-push of a grown idle session IS the server's incremental model. The client never tails byte offsets or diffs lines. The 30-minute idle gate prevents mid-conversation churn.

State file %APPDATA%/Cortex/local_ingest_state.json, schema v2:

```
{
  "version": 2,
  "bootstrap_done_at": iso8601 | null,
  "server_hashes_seen": [sha256, ...],
  "sessions": {
    "<session_uuid>": {
      "path": ..., "last_pushed_sha256": ..., "last_pushed_size": int,
      "last_pushed_mtime": float, "pushed_at": ..., "imported_id": str|null,
      "attempts": int, "last_error": str|null
    }
  },
  "last_scan_at": iso8601
}
```

Candidate logic: no record -> candidate. (size, mtime) match -> skip without hashing. Else hash; hash equals last_pushed_sha256 or in server_hashes_seen -> update record, skip. Otherwise candidate. Fresh-machine bootstrap: on bootstrap_done_at null, seed server_hashes_seen from list_imported_hashes before enqueuing; on failure proceed anyway (server hash-dedupe makes duplicates harmless, merely wasteful) and retry bootstrap next tick. Migration: fold a v1 pushed_hashes list into server_hashes_seen.

### 2.5 Upload spine: queue, retry, circuit breaker

Job shape: {id, kind: session_file|artifact|note, dedupe_key, src_path, filename, description, tags, payload, origin, created_at, attempts, next_retry_at, last_error, state: pending|dead}. dedupe_keys: session_file cc:\<uuid\>:\<sha256\>, artifact file:\<sha256\>, note note:\<origin_id\>.

- **Terminal-key ledger (FIX, HIGH):** the queue file persists a ledger of terminal dedupe_keys (succeeded and dead), bounded (keep the most recent 5000, age out beyond 90 days). enqueue() refuses any key present in the ledger as succeeded and any key currently pending. Without this, a producer that re-offers work (the media bridge re-polls unpushed sessions every 30s) would re-enqueue a note whose job already succeeded and left the queue, and notes have no server-side idempotency: the result is a duplicate note in the corpus every 30 seconds. The ledger is what makes note/artifact kinds effectively exactly-once; session_file gets exactly-once from server hash-dedupe as well, so for it the ledger is merely an optimization.
- **Sequential uploads only**, one in flight ever (matches the proven Hub behavior). Per-cycle cap ingest_max_per_cycle (default 20), inter-job delay ingest_upload_delay_seconds (default 3). Newest-first ordering so fresh context reaches the corpus before deep backlog.
- **Retry**: CloudUnreachable/ServerError -> backoff 1m, 5m, 15m, 60m, then hourly to max_attempts 12, then dead-letter (kept in queue file, tray shows failed count, manual retry action). ClientError -> straight to dead with the response body. AuthError -> no retry, auth latch.
- **Circuit breaker**: on CloudUnreachable, stop draining; probe health() every 60s (cap 5m); no attempt counts burned while open; tray goes OFFLINE gray. Laptop offline a week loses nothing.
- **Auth latch**: on 401/403 stop all uploads, sticky auth_error flag (tray ERROR red, "auth failed, update token"), re-probe only when config.json mtime changes.
- **Crash safety**: jobs removed only after handler success (and their key written to the ledger in the same atomic queue-file write); a crash mid-upload re-runs the job and the server answers skipped for session files. At-least-once delivery + server idempotency (session_file) + the terminal-key ledger (note/artifact) = effective exactly-once in the corpus.
- Handler for missing src_path: drop job, WARN.

RESOLUTION (Design 1's persistent JobQueue vs Design 3 CP1's "the scan loop is the retry queue"): both, staged. CP1 ships the simple model (failed file simply is not in pushed state and retries next scan, with a poison-file cooldown). The full JobQueue/uploader spine arrives with the Agent conversion in CP4 and becomes the single egress for sessions AND media. This keeps CP1 days-scale.

### 2.6 Config

%APPDATA%/Cortex/config.json via cortex_desktop/config.py merge-defaults (keep the mechanism). New canonical keys with load-time migration (read legacy pi_host/pi_password as fallback, write the new keys, leave legacy keys untouched so rollback to 0.2x works).

**Two-writer protocol (FIX):** config.json has two writers (the agent process: migrations, and the status window process: settings saves) and one mtime-watcher (the auth latch). Today's save is a plain truncate-write; a mid-write read can tear or clobber. Requirement: ALL config writes go through one shared helper in cortex_desktop/config.py that does read-merge-write (re-read the file, apply only the caller's changed keys, write tmp + os.replace). No other code path may write config.json.

**Dual-key writes until CP5 (FIX):** wifi_bridge.py (kept unconditionally, powers the micro-MCP) reads pi_host/pi_password until its CP5 port to the new keys. Any save of cloud URL or token (Section C of the status window, first-run setup) writes BOTH key sets (cloud_url + pi_host, cloud_token + pi_password) so the Agent and the micro-MCP can never point at different clouds. CP5 removes the dual-write when wifi_bridge moves to the new keys.

```
cloud_url: ""            (owner builds may prefill; customer frame ships empty, CP5)
cloud_token: ""
ingest_enabled: true
ingest_idle_minutes: 30
ingest_scan_interval_seconds: 300
ingest_max_per_cycle: 20
ingest_upload_delay_seconds: 3
ingest_max_attempts: 12
media_bridge_enabled: true
media_review_default: false
upload_original_media: false
update_channel: "stable"
whisper_model: "large-v3"        (absorbed from the Hub settings router)
whisper_force_cpu: false
vision_llm_url: ""               (section 4.5)
log_level: "INFO"
```

Shed keys: lmstudio_url, lmstudio_model, hub_port, hub_host, auto_open_browser. RESOLUTION (scan interval, Design 1's 300s vs Design 3 CP1's 15 min): 300 seconds is the config default everywhere; CP1 may ship 900 as its initial default if the team wants extra caution during backlog drain, but the key and the 300 target are the spec. RESOLUTION (kill switch, Design 1 keeps env vs Design 3 retires it): config key ingest_enabled is the normal control; env CORTEX_LOCAL_INGEST=0 remains as an emergency override that disables the watcher regardless of config. The old opt-IN semantics (=1 required) are retired.

---

## 3. The watcher (CP1, URGENT, ships alone within days as v0.22.0)

All inside the existing v0.21 Hub shell. No shed, no rename, no queue infrastructure. The corpus staleness (overseer tick fine, import_queue_depth=0) is fixed by this checkpoint alone.

**Cloud-side prerequisite (ships before or with CP1):** the one-line _import_one_jsonl fix from section 2.4 (delete the processed_imported_sessions row when replacing an existing imported_id). Without it, re-pushed grown sessions never re-gist and CP1 under-delivers on active sessions.

**Scope:**

1. Rewrite push_session_file() in cortex_local/ingester.py using the two-step protocol (section 2.3). Transport: cortex_mcp/wifi_bridge.py's config loader + _make_basic_auth_header + upload_file() (stdlib urllib, config read fresh every call). Base URL always from config; for the owner install that resolves to https://cortex.turfptax.com/core.
2. Client timeout 110s. Skip files over 50MB with a logged warning.
3. Dedupe bootstrap: on empty state, page GET {base}/plugins/overseer/imports?source=claude-code collecting file_hash into state (the _already_imported_hashes pattern from hub/backend/routers/overseer.py, degrade to empty set on failure).
4. Loop: the one-shot scan becomes a thread started from cortex_desktop/app.py, gated on ingest_enabled. Keep the 30-minute idle filter, whole-file sha256, atomic state writes, newest-first sequential pushes with the 3s inter-push delay. Mark state only on confirmed step-2 success (imported_id OR skipped).
5. Retry: absent-from-state = retried next scan. Poison-file cooldown: after 3 consecutive failures, skip for 6 scans; reset on hash change. Treat 429/503 as "stop this cycle, resume next scan". FIX: a step-2 failure that follows a successful step-1 does NOT count a poison strike immediately (a from-path parse exceeding the proxy's 120s window can 502 to the client while completing server-side; the next scan's re-push self-heals via skipped); it counts only if the next scan's re-push of the same hash also fails.
6. Logging: bring the rotating file logger forward into CP1 (%APPDATA%/Cortex/logs/agent.log). One INFO line per push outcome, one per scan summary.
7. Backlog drain: falls out of the first real run with zero special code. Every idle file not in state pushes, newest first, capped per cycle.

**Explicitly OUT of CP1:** Claude Desktop chats (Electron IndexedDB is unreadable; the supported route per overseer_3j is the Anthropic Data Export ZIP, handled in CP5, see section 4.1), live tailing, tray counts, the JobQueue.

**Verify:** (1) fresh state, one cycle: backlog uploads sequentially, cloud GET /core/plugins/overseer/imports count rises accordingly, overseer import_queue_depth goes nonzero and the tick consumes it. (2) Second scan: zero pushes. (3) Append to a test session, wait past the idle window: exactly that file re-pushes, server returns the same imported_id, AND the session re-enters the unprocessed set so the next tick re-gists the appended content (FIX: checking same imported_id alone would pass despite the re-process bug; this step verifies the cloud-side fix end to end). (4) Kill network mid-push: file retries next scan, no duplicate rows after recovery. (5) Port tests/test_import_path.py coverage onto the new push function.

**Rollback:** set ingest_enabled false (no reinstall), or install v0.21.0 from releases. The state file is inert either way.

---

## 4. Media parsing + upload

### 4.1 Intake

Sources: the status window drop zone, its Browse dialog, and the tray "Add Media File..." item. Accepted: AUDIO_EXTS + VIDEO_EXTS (lifted with the engine). A .zip matching the Anthropic Data Export pattern: in CP4, REJECT inline with a clear message ("Claude export ZIPs are supported in 1.1"); in CP5 it routes to the ingest path (section 8, CP5). FIX: the earlier "upload the ZIP via files/uploads for server-side handling" fallback is deleted; no cloud route ingests these ZIPs (only a dry-run endpoint exists), so uploaded bytes would silently never reach the corpus, which is worse than rejecting. IndexedDB parsing stays permanently out of scope. Anything else: inline reject, no job.

Intake writes %APPDATA%/Cortex/media_queue/\<uuid\>.json: {path, kind: audio|video|export_zip, review: bool, added_at, status: "pending"}. It references the original path, never copies the media (files can be GB); there is no staging directory (FIX: the scanner's earlier "dropzone staging dir" is deleted from the spec; media_queue job files are the only intake artifact). File gone at pickup -> error "File was moved or deleted". RESOLUTION (Design 2's media_queue files vs Design 1's JobQueue): the window writes intake files to media_queue/ (the IPC contract); the agent's scanner consumes them, runs the parse, and enqueues the RESULTS into the unified upload_queue. Two queues, two purposes: intake IPC vs upload egress.

### 4.2 Parse (agent side, one job at a time, serialized with ingest pushes)

- **Audio:** ffmpeg normalize to 16kHz mono WAV, then whisper-cli via cortex_agent/media/transcribe.py, including the full crash machinery (hard-crash codes, one CPU retry, sticky force_cpu). Keep the 500MB cap. Until ffmpeg is bundled (CP5), absent ffmpeg -> plain error row with the winget one-liner.
- **Video:** if the cortex-vision sidecar is installed and healthy (plugin_manager), submit the local path as a vision job and poll the session (capture -> PySceneDetect -> describe -> narrate -> ffmpeg audio extract -> whisper transcript). If vision is absent, unhealthy, or its LLM endpoint unconfigured, FALL BACK to audio-track-only: ffmpeg extract + whisper, tagged "video (audio only)". A missing local LLM degrades a drop to a transcript; it never blocks it.
- Progress (whisper -pp percent, vision session progress) lands in the media_queue job file, hence the window's job list.

### 4.3 Upload (via the spine)

MediaBridgeThread (port of hub/backend/services/video_overseer_bridge.py): every 30s, GET :8004/sessions?status=complete&pushed=false, re-filter client-side (vision's ?status= filter is broken upstream, keep the workaround), hydrate, then enqueue per session:

1. **kind=note**: narrative + a compact scene summary + transcript text (mode, scene count, source header; tags video,mode:\<m\>,session:\<id\>,scenes:\<n\>; note_type video-file|video-journal|video-live). **Fix the empty-narrative block**: when the narrative is absent (LLM unreachable), build the note from the deterministic concat of scene descriptions + spoken_text. Today's bridge returns False and the session never reaches the corpus; that is a bug, not a policy.
2. **kind=artifact**: the session's export.html (self-contained report with embedded thumbnails) via files/uploads, X-Filename video-session-\<id\>.html, X-Tags video,vision-export,session:\<id\>.

RESOLUTION (payload scope, Design 1's note+export.html vs Design 3's note with full scenes plus selected keyframes): the note carries the searchable text (narrative, scene summary, transcript); export.html is the single artifact carrying scenes, thumbnails, and full detail. Individual keyframe JPEGs are NOT uploaded separately (they are embedded in export.html). One artifact closes the "per-scene data never reaches the corpus" gap without N uploads.

3. **Per-session per-kind outcome tracking (FIX, HIGH):** the bridge keeps its own state map {session_id: {note: pending|succeeded|dead, artifact: pending|succeeded|dead}} and only enqueues kinds not yet succeeded; the queue's terminal-key ledger (section 2.5) backstops it (a re-offered succeeded key is refused at enqueue). Without both layers, a dead-lettered artifact would leave the session pushed=false and the 30s poll would re-enqueue the already-delivered note forever, spamming duplicate notes into a corpus that cannot dedupe them.
4. POST :8004/sessions/{id}/mark-pushed only after BOTH kinds for the session reach succeeded. Never on partial success, so a dead-lettered artifact keeps the session retriable (retriable for the artifact only, per the outcome map above).

**Audio terminus:** the transcribe engine enqueues kind=note (transcript text + metadata header: model, language, duration_s, source filename; tags audio,transcript; oversized transcripts: first N chars in the note + the full .txt as an artifact) and optionally kind=artifact for the original media when upload_original_media is true (default false).

### 4.4 Review path

Per-drop "Review before upload" checkbox (default from media_review_default, false). Reviewed jobs stop at awaiting_review; the window's [Review] opens a plain dialog: read-only transcript/narrative, buttons [Upload] [Edit then Upload] [Discard]. Edited text is what uploads. Discard deletes the job and temp files, nothing reaches the corpus. Dialogs are per-job, never batch; this surface must not grow list management. Default flow is AUTO (parse then upload, no prompt).

### 4.5 The vision LLM dependency

The LM-Studio CHAT is shed, but cortex-vision's Describe/Narrate stages still want an OpenAI-compatible endpoint. Default policy (proposed, open question 3): config key vision_llm_url, used when set (localhost:1234 if LM Studio happens to be present), stages degrade gracefully when not, and the empty-narrative fix guarantees degraded sessions still reach the corpus. Cloud-model routing is a later option, not a 1.0 blocker.

**Whisper handoff fix:** the Agent writes CORTEX_VISION_WHISPER_CLI into the sidecar's spawn environment (plugin_manager), permanently killing cortex-vision's fragile install-layout path detection (cortex_vision/audio/transcribe.py searches \<install\>\\_internal\backend\bin\whisper-cli.exe). This lands in CP3 and frees the bundle path to move in CP5.

---

## 5. Tray + status UX + first-run

### 5.1 Tray state machine

Reuse cortex_desktop/tray.py (create_icon_image logo + corner dot, 30s poll thread, dynamic update menu item). Extend the boolean dot to five states, one dot, five colors:

| State | Color | Meaning |
|---|---|---|
| SETUP | yellow (255,193,7) | No valid cloud URL + token (first_run, or saved config rejected). Outranks everything. |
| CONNECTED | green (76,175,80) | Health OK, no queued failures. Tooltip: "Cortex Agent: connected. N synced today". |
| SYNCING | blue (33,150,243) | Push in flight. Minimum 2s display. Overlays CONNECTED only. |
| OFFLINE | gray (158,158,158) | Network-class failures. Not an error: the queue holds everything. Tooltip: "offline, N items waiting". |
| ERROR | red (244,67,54) | Needs-user-action only: auth rejected, an item at 5+ consecutive push failures, or a parse failure on a user-dropped file. Outranks OFFLINE. |

State is computed in one place (cortex_local/status.py, alongside the ingest loop) and published to StatusBoard + status.json. The tooltip always carries a human sentence with a moving number; it is the number one "is it working" surface.

Tray menu: Status row (disabled, mirrors state), Open Status Window (default item, double-click), Add Media File..., Open Cortex Web (host root, strips /core), Update Available (vX) (dynamic, calls the lifted cortex_desktop/update_check.py in-process, never localhost HTTP), Copy MCP config, Quit. Removed: Open Cortex Hub, browser Settings link, localhost rows.

### 5.2 Status window

CortexAgent.exe --status-window: a separate process (third branch beside --mcp in app.py), tkinter (stdlib), single window ~420x560, three stacked sections, no tabs, no navigation. Separate process because pystray and tkinter both want a message pump on Windows, and it crash-isolates UI from ingest. Named-mutex single instance; a second launch focuses the first. Reads status.json every 2s; reads and writes config.json ONLY through the shared read-merge-write atomic helper (section 2.6; the Agent is a normal process, not UWP-sandboxed; wifi_bridge's read-only constraint does not apply here).

- **Section A, Status:** headline with dot glyph (same copy as tooltip), cloud host (never the token, never the /core path), last contact humanized, sessions synced today/total, media processed today, waiting-to-upload count, last 5 recent_errors as one-line rows (click opens the log in Notepad; this list is the ONLY error UI, no toasts). Buttons: [Test Connection] [Open Cortex Web] [Open Log]. Test Connection = the three-step probe from section 5.3 including the round-trip proof: send a test note via the sanctioned client, read it back, report "Working end to end" or name the failing step in words. This round trip is the one permitted corpus read.
- **Section B, Add Media:** bordered drop zone + [Browse...]. Drag-and-drop via tkinterdnd2 (bundle the tkdnd native lib in the spec); if it fails to import, degrade to Browse-only. Browse must always work; drag-drop is an enhancement, never a dependency. "Review before upload" checkbox. Below: current media jobs (pending/parsing/awaiting review/uploading + last 3 completed) with progress percent.
- **Section C, Settings (collapsed disclosure):** Cloud URL, Token (masked, show-on-hold), update channel radio (stable/dev), Start with Windows checkbox (toggles the Run key), [Save and Test] (never saves a failed token without an explicit "Save anyway"). Saves write both key sets per section 2.6 until CP5.

status.json contract (schema 1): state, state_text, cloud_host, last_ok_contact, last_push_at, sessions_today, sessions_total, media_done_today, queue_depth, recent_errors[{ts, kind, msg}], version, update_available, heartbeat. Timestamps local-with-offset per the standing time rule.

**Packaging (FIX):** cortex_desktop.spec currently lists tkinter in excludes; the status window would work from source and break in the installed exe. CP4 removes tkinter from the spec excludes, bundles the tcl/tk data files and the tkdnd native lib, and the CP4 verify list includes launching --status-window from the INSTALLED build, not just source.

### 5.3 First-run setup

Trigger: first_run true, or saved credentials 401 at startup. Tray starts SETUP yellow, auto-spawns the window with Section C expanded and a welcome header. The Agent appends /core if the user pastes a bare host; stored value is the full base URL. Validation on [Save and Test], each step reported in words: (1) "Reaching your cloud..." GET \<host\>/health at the gateway ROOT, unauthenticated (FIX: {base}/health sits under the /core proxy, which authenticates every path; probing it without a token always 401s, so step 1 strips /core); (2) "Checking your token..." GET {base}/health with Basic auth; (3) "Sending a test memory..." the round trip. On success: save cloud_url/cloud_token (both key sets, section 2.6), mark setup complete, dot green, header: "You're connected. You can close this window; the Agent runs in the tray." The CP1-style backlog drain then starts and the moving counters are the onboarding signal. Token storage: config.json plaintext matching today's posture; DPAPI wrap is explicitly deferred until before the first paid external install. Never log the token.

### 5.4 "Is it working?" for a non-technical user

Three concentric answers: GLANCE (green dot = working, the install guide's one sentence about health), HOVER (human tooltip with a moving number), CLICK (headline + Test Connection's end-to-end verdict naming the failing step). Error copy always names the thing in user terms plus the next action; exception text goes only to agent.log.

### 5.5 Anti-Hub guardrails (copy into repo docs)

- G1: the Agent PROCESS never listens on any port; no exceptions for "just status". Sidecars it spawns (cortex-vision on :8004) bind localhost only. (Kills the whole _wait_port_free/WinError-10048 class for the Agent itself, minimizes attack surface and customer firewall prompts, and structurally prevents "just one more tab".)
- G2: the window renders only connection state, counters, error lines, settings, media job rows, and per-job review text. Never corpus content.
- G3: the one permitted corpus read is the Test Connection round trip, fixed to its own test note.
- G4: any request for a new pane/list/view is answered with a link to the cloud web app; missing views get built there.
- G5: one window, three sections, plus the modal review dialog. A fourth section requires amending this spec first, deliberately.
- G6: no chat UI of any kind.

---

## 6. Strip-down

Dependency direction is favorable everywhere (shed code imports keep code, never the reverse). **Decouple first, delete second.** Wrong order breaks the tray updater, the sidecar lifecycle, and CI.

### 6.1 Decoupling steps (before any deletion)

- **D1, lift the update brain.** All update intelligence lives in hub/backend/routers/settings.py (check-update, _parse_version with dev.13 > dev.9 tuple sort and stable-outranks-prerelease, _extract_release_assets, apply-update with git-pull fallback) and the tray reaches it over localhost HTTP. Move the ~250-line block to cortex_desktop/update_check.py (deps: httpx + the already payload-agnostic cortex_desktop/updater.py). Keep reading __version__ from cortex_desktop/__init__.py (two-file lockstep with pyproject.toml). Channel becomes config key update_channel.
- **D2, config re-home.** cortex_desktop/config.py becomes the single survivor: absorb whisper_model/whisper_force_cpu from the settings router, add cloud_url/cloud_token with legacy fallback migration, re-home the 25-line test-connection helper, and implement the atomic read-merge-write helper (section 2.6) that ALL writers use.
- **D3, extract the media engines.** transcribe.py engine to cortex_agent/media/transcribe.py (section 2.2). plugin_manager, video_overseer_bridge, pi_client relocate. Critical: their STARTUP is owned by hub/backend/main.py's lifespan context manager, not by routers; that ownership moves to the Agent's ServiceLoopThread or nothing spawns the sidecar. Port pi_client off hub/backend/config.py pydantic settings.
- **D4, whisper bin staging.** Move the SOURCE staging dir from hub/backend/bin/ to cortex_desktop/bin/ in scripts/build_whisper_cpp.py, but keep the BUNDLED destination backend/bin inside the exe layout until CP3's CORTEX_VISION_WHISPER_CLI env handoff lands (cortex-vision searches the old layout). Changing the script invalidates the CI whisper cache key once; expected.
- **D5, rewrite app.py main().** Keep: --mcp branch, console-hide via ctypes, load_config, tray-on-main-thread, shutdown_event + 3s force-timer, SIGINT handler. Add: --status-window branch, watcher thread, ServiceLoopThread. Delete: _find_backend_dir, _find_frontend_dist, _start_server, _wait_port_free, browser auto-open. Net ~100 lines.
- **D6, lemon gate.** hub/backend/services/lemon_export.py + routers/lemon.py are cloud-to-cloud traffic that merely runs on the desktop, but they are the ONLY live Lemon transport. Do not delete until Tory confirms rehoming to Cortex-Cloud or accepts an outage. The one shed item with an external liveness consequence.

### 6.2 Delete list (after D1..D6)

| Path | Note |
|---|---|
| hub/frontend/ | entire React SPA, 67 ts/tsx, 141MB node_modules |
| hub/backend/routers/overseer.py | 1698-line core proxy; manual import UI replaced by the watcher |
| hub/backend/routers/chat.py + services/lmstudio.py | the LM-Studio chat |
| hub/backend/routers/data.py, intro.py, pi.py | cloud-owned surfaces; gateway serves /intro since 2026-07-12 |
| hub/backend/routers/transcribe.py + video.py | the router shells left after D3 extraction; the engines live on in cortex_agent/media/ and media_bridge |
| hub/backend/routers/plugins.py | slims to the admin surface for the window's job rows only (no marketplace UI); the router itself deletes |
| hub/backend/routers/voice.py + services/voice_agent_manager.py + voice_agent/ | voice imports FROM transcribe, so D3 first |
| hub/backend/routers/lemon.py + services/lemon_export.py | gated on D6 |
| hub/backend/main.py + config.py + remaining scaffolding | after lifespan migration; keep the log ring buffer idea via agent.log |
| cortex_desktop.spec entries | backend datas, frontend_dist/, training/ dir, cortex_train.* + serial hiddenimports (dead since v0.21.0), uvicorn/fastapi/starlette/pydantic-settings hiddenimports; REMOVE tkinter from excludes and add tcl/tk data + tkdnd (section 5.2 packaging) |

Survivors relocated: pi_client.py, plugin_manager.py (+ the slimmed admin surface), video_overseer_bridge.py (as media_bridge), the transcribe engine, cortex_local/, cortex_mcp/wifi_bridge.py + protocol.py. scripts/build_whisper_cpp.py and check_no_personal_data.py stay; scripts/video-annotator/ stays out of the installer and is the standing reason for the no-add-A rule.

### 6.3 cortex_mcp verdict

**Slim to a micro-MCP; do not delete outright. Keep wifi_bridge.py unconditionally.** ~45 of the ~55 tools in cortex_mcp/server.py are duplicated by the cloud gateway's 14 MCP tools; serving them locally creates drift and split-brain. Three capabilities are cloud-irreplaceable because they touch local disk: file_upload/file_download, raw table CRUD, audits. v1.0.0 ships server.py cut to file_upload, file_download, ping/status. Raw table CRUD + audits default to drop pending Tory's call (open question 2). The --mcp branch and console=True in the spec stay (console=True is REQUIRED for stdio; tray mode hides its own window). wifi_bridge reads the legacy keys until its CP5 port; the dual-write rule in section 2.6 keeps it aligned meanwhile. Killing the micro-MCP later is a one-release decision; resurrecting it would be a multi-release regret.

---

## 7. CI, release, versioning, migration

### 7.1 CI (.github/workflows/build-release.yml)

Keep unchanged: push-to-master + tag v* triggers, pytest gate before expensive steps, Vulkan SDK install (jakoch/install-vulkan-sdk-action@v1.4.0, SDK v1.3.290.0), whisper-cli cache keyed on build_whisper_cpp.py hash, long-paths registry fix, iscc /DMyAppVersion, prerelease regex -(dev|rc|alpha|beta) feeding the softprops prerelease flag, version-from-tag. Changes: delete setup-node@v4 + npm ci/build steps; build.py loses build_frontend()/copy_frontend_dist() and the PyInstaller step becomes its only behavior. Treat cortex_desktop.spec + build.py + installer.iss + build-release.yml as ONE atomic change set in the CP4 PR or CI red-lights.

### 7.2 Versioning

**Two-stage (RESOLUTION, adopting Design 3; Designs 1 and 2 describe the CP4 end state):** the urgent watcher ships as **v0.22.0** inside the existing Hub shell; the conversion ships as **v1.0.0 "Cortex Agent"**. Rationale: the corpus is starving NOW, and coupling a days-scale fix to a weeks-scale strip-down is the classic mistake; meanwhile removing the entire Hub UI is a textbook breaking change and the paid-service story needs a clean "install Cortex Agent 1.x" epoch. _parse_version's numeric tuple sorts 1.0.0 above 0.22.0, so the auto-update chain carries users across with zero special-casing, given asset-name continuity below. Version stays two-file lockstep (pyproject.toml + cortex_desktop/__init__.py, currently 0.21.0).

### 7.3 Migration mechanics (Hub install to Agent install)

1. **Asset-name continuity (the one sharp edge):** installed 0.2x clients match CortexHub-Setup-*.exe only. The v1.0.0 release MUST publish its installer as CortexHub-Setup-1.0.0.exe even though the product inside is Cortex Agent. update_check.py in 1.0.0+ matches CortexAgent-Setup-*.exe with a CortexHub-Setup-*.exe fallback. v1.1.0 onward publishes the new name only.
2. **Inno upgrade-in-place:** keep AppId GUID B8F3D2A1-... forever. installer.iss: AppName "Cortex Agent", exe CortexAgent.exe, PrepareToInstall taskkills CortexHub.exe AND CortexAgent.exe AND cortex-vision.exe (drop the port-8003 Sleep(5000) rationale), [UninstallRun] updated, delete the old {userstartup} CortexHub shortcut.
3. **Config:** survives on disk; first 1.0.0 launch runs the D2 migration. Owner installs reconnect with zero user action. Stale keys ignored, not deleted, so 0.2x rollback still works.
4. **State:** local_ingest_state.json is additive; even a lost state file costs one wasteful re-scan, never duplicates.
5. **Release notes** must say: the local Hub UI is removed; your corpus lives at your cloud instance; this app is now the background Agent feeding it; settings carried over automatically; vision and transcription still work, now uploading to the cloud; localhost:8003 no longer exists.
6. **MCP config:** Claude Desktop configs pointing at CortexHub.exe --mcp need the new path; release notes carry the one-liner, the tray gets a "Copy MCP config" action replacing the old /settings/mcp-config endpoint.

### 7.4 Startup-at-boot + resilience

Replace the opt-in unchecked Startup-folder shortcut with an **HKCU Run registry key** (Software\Microsoft\Windows\CurrentVersion\Run, value CortexAgent = quoted exe path), Inno [Registry] entry, **default ON** with an installer opt-out task, removed on uninstall. Per-user, no UAC. The window's "Start with Windows" checkbox toggles the same key. **Crash resilience:** wrap watcher/uploader/service loops in catch-log-continue supervisors (a dead thread restarts itself with backoff). No Windows service, no scheduled-task watchdog in 1.0; Run key + in-process supervision + next-logon relaunch is proportionate. Persistent logging per section 2.2 is a 1.0 requirement (brought forward to CP1).

---

## 8. Checkpoint sequence

Each CP independently shippable and rollback-able.

**CP0 (cloud-side, one line, ships before or with CP1):** the _import_one_jsonl re-process fix from section 2.4 in Cortex-Cloud. Verified by CP1's verify step 3.

**CP1, v0.22.0: minimal watcher (URGENT, days).** Scope, verify, rollback: section 3. Ships alone, waits only for CP0.

**CP2, v0.23.0: shell decoupling (Hub still present).** Scope: D1 (update_check.py, tray calls in-process, update_channel key), D2 (config migration + whisper keys + test-connection helper + the atomic read-merge-write config helper). Hub endpoints keep working by delegating to the lifted module; nothing user-visible changes. Verify: tray check/apply update works with the Hub backend process killed; version-comparison unit tests carried over (dev.13 > dev.9, stable outranks same-base prerelease); config round-trip: legacy-only file gains new keys on load, values match; concurrent-writer test: two processes saving disjoint keys through the helper lose nothing. Rollback: revert tag; the migration is additive, old builds still read pi_host/pi_password.

**CP3, v0.24.0: media terminus.** Scope: D3 + D4; transcribe engine extracted with a cloud-write terminus (note with transcript + metadata, optional raw archive via files/uploads); video_overseer_bridge generalized per section 4.3 including the empty-narrative fix, the client-side status re-filter, and per-session per-kind outcome tracking; PluginManager + bridge ownership moves to the service loop (still invoked from the Hub process this release); CORTEX_VISION_WHISPER_CLI written into the sidecar spawn env; vision_llm_url config key. Verify: local video through the sidecar lands narrative + scenes + transcript in the corpus and export.html in files, mark-pushed sets; repeat with the LLM stopped: session still pushes (transcript + empty descriptions); simulate an artifact failure with a delivered note: no duplicate note appears on subsequent polls, session stays retriable for the artifact only; audio file: note with correct metadata; force a hard-crash code: CPU retry + sticky force_cpu fire from the library context. Rollback: revert tag; notes and file uploads are unchanged server primitives.

**CP4, v1.0.0 "Cortex Agent": strip-down + rebrand.** Scope: D5, D6 resolution, full delete list (6.2), CI changes (7.1), migration mechanics including the CortexHub-Setup-1.0.0.exe asset name, Run key + shortcut cleanup, micro-MCP, exe/installer rename, dependency diet (drop fastapi, uvicorn, websockets, pydantic-settings; keep httpx, pydantic, pystray, Pillow, mcp, click, tkinter stdlib + optional tkinterdnd2; remove tkinter from spec excludes and bundle tcl/tk data + tkdnd), the JobQueue/uploader spine with the terminal-key ledger replacing CP1's simple retry, the five-state tray, the status window, first-run flow (gateway-root health probe for step 1), media drop flow (export ZIPs rejected with the "supported in 1.1" message, section 4.1), dual-key config writes (section 2.6). Version lockstep bump in both files. Verify (the big one, on a real 0.22/0.23 machine): tray shows update, applies, relaunches as CortexAgent; config migrated; ingest resumes from existing state with zero re-uploads; sidecar spawns and finds whisper via the env var; model dir untouched; old shortcut gone, Run key present; --mcp serves the micro tool set AND still reaches the cloud after a token change made in the status window (dual-write proof); --status-window launches from the INSTALLED build including drag-and-drop, and degrades to Browse-only if tkdnd is removed; uninstall clean; CI tag build green with no Node step; pytest covers update_check, config migration, ingester push, queue persistence + ledger refusal of succeeded keys. Rollback: previous installers stay on the releases page; installing 0.2x over 1.0.0 works (AppId unchanged, legacy config keys kept); documented in release notes.

**CP5, v1.1.0: deployability + polish.** Scope: remove the turfptax.com default everywhere (wifi_bridge/config); port wifi_bridge to cloud_url/cloud_token and retire the dual-write; cloud_url defaults empty, first-run dialog covers it (existing installs unaffected); rename assets to CortexAgent-Setup-*.exe / CortexAgent-windows-x64.zip; **ffmpeg bundling** (LGPL shared ffmpeg.exe into the bin staging dir, spec bundle, transcribe library + sidecar env taught the path with PATH fallback, license noted in the installer; this is new work and the reason it is not in 1.0); repoint or retire scripts/mine_video_journals.py + index_videos.py (they target the deleted port 8003); **Claude Desktop export-ZIP support** with the parsing location named (FIX): the AGENT unzips locally and converts each conversation to a per-conversation payload pushed through the existing session import path, unless the cloud grows a real ingest endpoint first (today only a dry-run route exists; ZIPs are never blind-uploaded to files/uploads); optionally move the whisper bundle path out of backend/bin now that the env handoff removed the coupling. Cloud-side sibling task (already flagged in memory): parameterize the "Tory Moghadam" MCP branding for friend/customer instances. Verify: clean VM, no config: install, first-run dialog, point at a test instance, ingest works; ffmpeg-absent machine uses the bundled one; a 1.0.0 machine updates across the asset rename; an export ZIP lands its conversations in the corpus. Rollback: tag revert; the update module's name fallback keeps even a partial rollback updatable.

---

## 9. Risks + open questions for Tory

**Risks:**

- **The 120s proxy window vs large files.** The gateway /core read timeout caps uploads at what fits ~110s of upstream bandwidth. Mitigated by the 50MB line and oversize dead-lettering; if field data shows real sessions above it, the fix is a chunked or resumable upload endpoint cloud-side, not client heroics. The same window can 502 a slow step-2 parse that succeeds server-side; section 3 item 5 keeps that from burning poison strikes.
- **Migration chain fragility.** The whole install base crosses the rename on one release whose asset name must lie (CortexHub-Setup-1.0.0.exe). Mis-naming that one asset strands 0.2x installs on manual update. Mitigation: it is a release checklist line item, and the fallback matcher in update_check.py is tested in CP2.
- **cortex-vision layout coupling.** Until CP3's env handoff ships, moving the bundled whisper path breaks vision's transcription silently (it degrades to skipped transcription, not failure). D4's two-phase move exists precisely for this; do not collapse it.
- **CP0 dependency.** CP1's value proposition (fresh appended content reaches the corpus) depends on the one-line cloud fix; the client-side watcher alone cannot compensate. It is deliberately sequenced as CP0 so it cannot be forgotten.

**Open questions (decisions gate the marked steps; everything else proceeds on the stated defaults):**

1. **Lemon transport (gates D6/CP4 deletion):** confirm rehoming lemon_export to Cortex-Cloud before the desktop copy is deleted, or accept an outage window. It is currently the only live Lemon path.
2. **Micro-MCP final tool set (gates CP4 cut line):** spec ships file_upload, file_download, ping/status. Keep or drop raw table CRUD and the audit/weekly_review suite? Default: drop; the cloud web app is the corpus surface.
3. **Vision LLM policy (gates nothing, shapes CP3 default):** config-driven vision_llm_url with graceful degradation (proposed default), versus routing descriptions through a cloud model. Degraded sessions reach the corpus either way after the empty-narrative fix.

Decided defaults folded in unless overridden: media flow is auto-upload with a per-drop review checkbox; tkinterdnd2 is the one optional UI dependency (Browse always works without it); raw-media upload flag exists but defaults off; DPAPI token wrapping is deferred until before the first paid external install.

**Flagged for later, do not build now (Tory, 2026-07-24): local redaction before upload.**
If local ingestion ever needs redaction before content reaches the cloud, the Agent is
the layer that does it, via a LOCAL model, so nothing sensitive ever leaves the machine.
The default path stays exactly as specified in this plan (push as-is; the cloud's
sensitivity tiers + confidential-tier sanitizer remain the gate); the mechanism gets
designed when a concrete case exists. Two constraints for implementers NOW so the spine
never precludes it:
1. The seam is a single optional transform hook in the upload spine, applied to payload
   bytes at UPLOAD time (between dequeue and the HTTP call), never at scan time. The
   watcher, queue, and state files stay redaction-unaware.
2. Dedupe and watch state stay keyed on the SOURCE file hash, never the pushed bytes,
   so a transform (or a later change to one) cannot break idempotency or trigger
   re-push storms.
The Agent already runs local models (whisper.cpp, the vision sidecar), so a small local
redaction model is congruent with the architecture; candidate wiring is a per-source
config flag naming a local endpoint, off by default.

Key files: C:\dev\ttx\Cortex\cortex-desktop\cortex_local\ingester.py, C:\dev\ttx\Cortex\cortex-desktop\cortex_mcp\wifi_bridge.py, C:\dev\ttx\Cortex\cortex-desktop\hub\backend\routers\{overseer,transcribe,settings,video,plugins}.py, C:\dev\ttx\Cortex\cortex-desktop\hub\backend\services\{plugin_manager,video_overseer_bridge,pi_client}.py, C:\dev\ttx\Cortex\cortex-desktop\cortex_desktop\{app,tray,config,updater}.py, C:\dev\ttx\Cortex\cortex-desktop\{cortex_desktop.spec,build.py,installer.iss,pyproject.toml}, C:\dev\ttx\Cortex\cortex-desktop\.github\workflows\build-release.yml, C:\dev\ttx\Cortex\cortex-desktop\scripts\build_whisper_cpp.py, C:\dev\ttx\Cortex\Cortex-Cloud\gateway\cortex_gateway\rest\core_proxy.py, C:\dev\ttx\Cortex\Cortex-Cloud\core\plugins\overseer\__init__.py (CP0 fix site), C:\dev\ttx\Cortex\Cortex-Cloud\core\plugins\overseer\overseer_db.py, C:\dev\ttx\Cortex\cortex-vision\cortex_vision\audio\transcribe.py.
