# Desktop cloud-migration prep: inventory, local ingester spec, cutover plan

Status: PREP + DESIGN ONLY (2026-07-20). The client flip is gated on core P3
(the cloud Container App actually deployed). Nothing in this document is wired
yet; the Pi path keeps working unchanged. Role doc: `CLOUD_MIGRATION.md`.
Canonical cross-repo direction: `cortex-core/docs/CLOUD_MIGRATION.md`.

---

## 1. Inventory: what the Pi-proxy Hub backend does today

The FastAPI backend (`hub/backend/`) is started by the tray app
(`cortex_desktop/app.py`) as a background uvicorn thread on `127.0.0.1:8003`.
It serves the React SPA from `frontend_dist/` and mounts these routers
(`hub/backend/main.py:205-221`):

| Prefix | Router file | What it does | Pi-coupled? | Fate at cutover |
|---|---|---|---|---|
| `/api/chat` | `chat.py` | LM Studio chat proxy (SSE) to LAN box `10.0.0.102:1234` | No (LAN LM Studio) | RETIRE (cloud chat goes through the cloud core / OpenRouter). Revisit only if local-model chat is wanted. |
| `/api/training` | `training.py` | Training pipeline steps, dream cycle, datasets. Step 00 SCPs `cortex.db` off the Pi | Yes (data source) | KEEP LOCAL, RE-SOURCE later: GPU training stays on this machine, but step 00 must pull the corpus from cloud (Litestream restore or an export endpoint). Out of scope for the flip itself. |
| `/api/pi` | `pi.py` | Pi command proxy, pet care, tuck-in, Pi firmware update/rollback | Entirely | RETIRE (no Pi) |
| `/api/games` | `games.py` | Pong RL training jobs | Pi tab UI only | RETIRE with the Pi tab (decide: keep as local toy) |
| `/api/data` | `data.py` | Pi database browser (tables/query/upsert/delete) | Entirely | RETIRE (cloud gateway serves reads; admin writes via cloud surface) |
| `/api/learning` | `learning.py` | LM Studio synthesis management (multi-server work stealing) | No (LAN) | RETIRE / decide with `/api/chat` |
| `/api/settings` | `settings.py` | App config, network scan, update check/apply (GitHub releases), MCP setup | No | STAYS LOCAL: whatever local shell remains (tray + ingester) still needs config and self-update |
| `/api/overseer` | `overseer.py` | ~120 routes, almost all thin proxies to the Pi's `/plugins/overseer/*` | Entirely, EXCEPT the two local-file routes below | RETIRE the proxy layer (the GUI hits the cloud gateway directly). CARVE OUT: `GET /scan/claude-code` + `POST /import` (see section 3) |
| `/api/transcribe` | `transcribe.py` | Local whisper.cpp transcription (async, progress polling) | No | STAYS LOCAL (or moves to cloud later, TBD by cost/latency per the role doc) |
| `/api/voice` | `voice.py` | STT/TTS + Pipecat voice agent lifecycle | Partly | DECIDE at flip (depends where the voice agent's LLM lives) |
| `/api/plugins` | `plugins.py` | Local sidecar plugins (cortex-vision): install, health, respawn | No (local exes) | STAYS LOCAL (machine-local camera/vision by nature) |
| `/api/video` | `video.py` | Video ingest + live WS; `video_overseer_bridge` pushes to Pi | Yes (push target) | RE-POINT push target to cloud, or retire bridge |
| `/api/lemon` | `lemon.py` | Lemon Squeezer export: pulls graded dispatches FROM the Pi, POSTs to local `lemon serve` | Yes (pull source) | RE-SOURCE from cloud gateway, or retire |
| `/intro` | `intro.py` | HTML intro page | No | Moves to cloud with the GUI |
| `/` static | `main.py` | Serves the React SPA | No | GUI moves to cloud hosting |

Background services started by the backend (`main.py` lifespan):

| Service | Interval | What it does | Fate |
|---|---|---|---|
| Plugin health loop (`plugin_manager.py`) | 5 s | Health-checks + respawns local sidecars | Stays local |
| Video overseer bridge (`video_overseer_bridge.py`) | 30 s | Forwards video-derived events to the Pi overseer | Re-point or retire |
| Lemon exporter (`lemon_export.py`) | 900 s (opt-in, `lemon_export_enabled`) | Pi -> local Lemon Squeezer egress | Re-source or retire |

Config surface (`hub/backend/config.py`, resolution env > config.json >
default): `pi_host/pi_port/pi_username/pi_password` (Basic Auth `cortex:cortex`
to `10.0.0.25:8420`), `lmstudio_url/lmstudio_default_model`, `host/port`,
`whisper_model/whisper_force_cpu`, `lemon_*`, training dirs. Env prefix
`CORTEX_HUB_*`. User file: `%APPDATA%/Cortex/config.json`.

Retirement shape: the whole Pi-proxy layer is `services/pi_client.py` plus the
routers marked RETIRE. No other repo imports them. The GUI's `apiFetch()`
(`hub/frontend/src/lib/api.ts`) is the single seam where `/api/...` becomes the
cloud gateway base URL + OAuth bearer.

## 2. Inventory: what the live-forward daemon forwards

"The daemon" is `cortex_mcp/daemon.py`, the shared-serial TCP server
(localhost:19750) that owns the ESP32 dongle port. It has THREE roles; only one
retires outright:

1. Serial multiplexing for MCP clients (Claude Code / Desktop / cortex-cli
   sending `CMD:` lines to the Pi over BLE). Dies with the Pi as a target;
   the transport itself is the phone-bridge fallback below.
2. Phone-bridge responder (`_answer_phone_cmd`, daemon.py:164): answers
   phone-originated `CMD:ping` / `CMD:echo` arriving via
   phone -> BLE -> cortex-link -> USB-CDC.
3. Sync live-forward (`_answer_sync`, daemon.py:197 + `cortex_mcp/gateway.py`):
   THIS is what the migration retires.

The live-forward, precisely (contract v2, `SYNC_CONTRACT_DRAFT.md`, RATIFIED
2026-06-10):

- Trigger: event-driven only. A phone-originated `CMD:sync_push:` /
  `CMD:sync_pull:` / `CMD:sync_status:` line arrives on the dongle serial.
  There is NO schedule, cron, or spool on the desktop side.
- Action: stateless HTTP relay to the Azure gateway per the transport mapping:
  `sync_push` -> `POST {gateway}/v1/sync/push`, `sync_pull` ->
  `POST {gateway}/v1/sync/pull`, `sync_status` ->
  `GET {gateway}/v1/sync/status?device=`. Bearer token, `app` scope,
  20 s timeout. The gateway JSON response is relayed back verbatim as
  `RSP:<kind>:<json>`; unreachable/unprovisioned gateway answers
  `ERR:<kind>:offline` and the phone keeps rows queued.
- Payloads (contract v2): push batches carry `device` + rows with
  client-generated uuid `id` (idempotency key), kinds
  `human_journal_entries` and `notes` phone->corpus; pulls use opaque
  cursors for `summaries_gist` and `temporal_narratives` corpus->phone.
- Config: `CORTEX_GATEWAY_URL` / `CORTEX_GATEWAY_TOKEN` env, falling back to
  `gateway_url` / `gateway_token` in `%APPDATA%/Cortex/config.json`
  (`cortex_mcp/gateway.py:32`). Currently unprovisioned on this machine, so
  every forward already reports offline.

Why it retires cleanly: in the cloud model the phone talks to the same
`/v1/sync/*` endpoints directly (gateway-primary transport). The desktop relay
only ever added value when the phone had no internet but the desktop did,
reached over BLE. That path demotes to opt-in per the canonical doc; removing
`_answer_sync` + `gateway.py` cannot lose data because the desktop never
spooled anything.

Reversibility note: the code to delete at cutover is exactly
`cortex_mcp/gateway.py` and the `CMD:sync_*` branch of
`daemon.py:_answer_phone_cmd` (plus `tests/test_phone_bridge.py` sync cases).
Everything else in the daemon is the BLE fallback, kept until mobile confirms
the demotion.

## 3. The local Claude-file ingester (the piece that STAYS)

The one job only a process on this machine can do: Claude Code and Claude
Desktop (code mode) write conversation `.jsonl` files here; watch them and push
new/changed sessions to the cloud.

### What exists today (to carve out of `routers/overseer.py`)

- Scanner (`GET /api/overseer/scan/claude-code`, overseer.py:852): walks
  `~/.claude/projects/<encoded-project-dir>/*.jsonl`, per file computes
  sha256, session_id (= filename stem, the Claude session UUID),
  project_folder, size, mtime; marks `already_imported` by hash match against
  the corpus's `imported_sessions` table.
- Importer (`POST /api/overseer/import`, overseer.py:1030): per path, hash,
  skip if known, else upload the raw file
  (`POST {pi}/files/uploads`, headers `X-Filename`, `X-Description`,
  `X-Tags: claude-code,overseer-import`) and trigger
  `POST {pi}/plugins/overseer/imports/from-path {path, source}`. Sequential,
  600 s upload timeout. Dedup is hash-based and idempotent.
- Claude Desktop's chat conversations (Electron IndexedDB at
  `%APPDATA%/Claude/IndexedDB/`) are NOT covered; known deferred follow-up.

### Target shape: standalone `cortex_local` agent

A small headless process (tray-launched or scheduled), no Hub backend needed:

1. Watch `~/.claude/projects/` (poll mtimes on an interval; a file watcher is
   an optimization, not a requirement; sessions are append-only .jsonl).
2. Debounce: only push a session file once it has been idle for N minutes
   (Claude appends to the live session; pushing mid-conversation re-uploads
   the file on every change. Idle threshold default 30 min; re-push on later
   growth is fine because dedup is by content hash).
3. Push to the cloud gateway, auth via loopback OAuth (below).
4. Keep local state (`%APPDATA%/Cortex/local_ingest_state.json`): pushed
   hashes + last scan time, so restarts are cheap and blind re-push stays
   harmless.

### Gateway contract it needs (ASK for the gateway stream, not built yet)

The gateway's current write path (`POST /v1/ingest`, rest/corpus.py:93) is
note-shaped (`{content, kind, tags, project}`), not a session-file intake.
Proposed addition, mirroring the Pi flow the overseer already understands:

- `POST /v1/imports/session-file`: raw `.jsonl` body
  (`Content-Type: application/octet-stream`), headers `X-Filename`,
  `X-Source: claude-code`, `X-File-Hash` (sha256). Gateway writes the file to
  the shared volume and triggers the co-located core's import
  (same code path as the Pi's `/plugins/overseer/imports/from-path`).
  Response: `{ok, imported_id | skipped, session_id, message_count}`.
- `GET /v1/imports?source=claude-code&limit&offset`: rows with `file_hash`,
  for authoritative dedup on a fresh machine (mirrors the Pi's
  `/plugins/overseer/imports`).
- Idempotency: server-side skip on known `file_hash`, exactly like today.

### Loopback OAuth plan (RFC 8252, the flow the gateway already supports)

Gateway facts (verified in `cortex-gateway/cortex_gateway/oauth.py`):

- Discovery: `GET /.well-known/oauth-authorization-server` returns
  `authorization_endpoint` (`/oauth/authorize`), `token_endpoint`
  (`/oauth/token`), `registration_endpoint` (`/oauth/register`),
  `token_endpoint_auth_methods_supported: ["none"]` (public client + PKCE).
- Loopback redirects (`http://127.0.0.1:PORT/...` or localhost) are accepted
  only when `GATEWAY_OAUTH_ALLOW_LOOPBACK=1` is set on the gateway, and
  matching is port-agnostic per RFC 8252, so the client may bind an ephemeral
  port at runtime.
- Authorization code TTL 60 s, consent screen TTL 300 s, PKCE S256 required,
  single-use codes enforced.

Ingester flow:

1. First run: `POST /oauth/register` with
   `{client_name: "cortex-local-ingester", redirect_uris:
   ["http://127.0.0.1/callback"]}`; store `client_id`.
2. Bind `127.0.0.1:<ephemeral>` with a one-shot HTTP listener; generate PKCE
   verifier/challenge; open the system browser at `/oauth/authorize?...` with
   `redirect_uri=http://127.0.0.1:<port>/callback`.
3. Owner signs in / approves consent; listener catches `?code=`.
4. `POST /oauth/token` (form-encoded, `client_id`, `code`, `code_verifier`,
   `redirect_uri`); store the bearer.
5. Token storage: Windows DPAPI (Credential Manager via `keyring`, or
   `CryptProtectData`), user-scoped. Never in config.json, never the browser.

Two blockers to resolve with the gateway stream BEFORE the flip
(also flagged in `team-mail/TO_MOBILE.md`):

1. SCOPE GAP: OAuth-minted tokens are capped to `connector:read` /
   `connector:write` (`oauth.py:_clean_scope`); only the allowlisted Hub
   redirect client is minted the elevated `hub` scope. But `/v1/sync/*`,
   `/v1/ingest`, and the proposed `/v1/imports/*` all require `app` scope.
   The ingester's loopback client therefore cannot call its own endpoint.
   Options: (a) allowlist the ingester as a trusted client that mints
   `app`/`hub` scope after owner consent, or (b) add an owner-native-app
   grant path. Gateway stream's call.
2. NO REFRESH TOKENS yet: the ingester pushes headlessly in the background
   and cannot pop a consent screen when a short-lived access token dies.
   Same blocker already recorded for the phone (TO_MOBILE 2026-07-11 item 3).
   The ingester should ship after refresh lands, or with a long-ish token
   TTL as an interim.

### Scaffold in this repo (behind a flag, does nothing by default)

`cortex_local/ingester.py`: standalone scanner + state tracking + a push stub
that refuses to run until `CORTEX_LOCAL_INGEST=1` AND a gateway URL is
configured, and even then the push raises until the gateway endpoint exists.
Dry-run scan only (`python -m cortex_local.ingester`). No Hub backend import,
no Pi import, stdlib only. This is deliberately NOT wired into the tray app.

## 4. Cutover checklist (the day core P3 lands)

Preconditions (verify before touching anything):

- [ ] Cloud app healthy: `GET {cloud}/health` green; OAuth discovery resolves.
- [ ] Corpus data migrated (core P4): row counts for `imported_sessions`,
      `summaries_gist`, `human_journal_entries` match the Pi.
- [ ] Gateway has `GATEWAY_OAUTH_ALLOW_LOOPBACK=1` and the ingester
      scope/refresh answers from section 3.
- [ ] The gateway session-file import endpoint exists and a manual curl of one
      .jsonl imports end-to-end (file -> gist visible via `/v1/search`).

Flip (each step independently reversible):

1. Env/URL swaps: add `gateway_url = https://<cloud-app>` to
   `%APPDATA%/Cortex/config.json`. Leave every `pi_*` key in place untouched
   (they are the rollback).
2. GUI: point the browser (or tray webview) at the cloud URL. The local
   `/api/*` proxy stops being the GUI's backend.
3. Turn ON: `CORTEX_LOCAL_INGEST=1`, run the ingester's OAuth first-run, then
   its watch loop. Confirm one fresh Claude session lands as a cloud gist.
4. Turn OFF, in order:
   a. Live-forward: remove `CORTEX_GATEWAY_TOKEN` provisioning from the
      daemon (phone now syncs direct); daemon keeps serving BLE ping/echo
      until mobile confirms the fallback demotion.
   b. Tray-launched Hub backend: stop auto-starting uvicorn (tray keeps:
      updater, ingester lifecycle, transcribe if still local).
   c. Lemon exporter + video bridge: re-point at the cloud or disable.
5. Pi: leave it RUNNING but idle for the soak week. Nothing writes to it after
   step 2-4, which is itself a rollback asset.
6. Verify: one full day cloud-only with the Pi powered OFF: chat, search,
   journal write, a temporal narrative tick, and an ingester push all green.

Rollback (any step fails):

- GUI back to `http://localhost:8003` (backend still installed; re-enable tray
  autostart if 4b was done).
- `CORTEX_LOCAL_INGEST=0`; the Hub's scan/import panel still works against the
  Pi because the pi_* config was never removed.
- Power the Pi back on; it was never wiped. Data written cloud-side during the
  attempt re-imports to the Pi by the same hash-dedup import if ever needed.
- Only after N green days: retire the proxy routers + daemon sync branch in
  code (section 2 lists the exact deletions) and wipe the Pi.

## 5. Open questions (tracked, not blocking prep)

- Thin tray launcher vs pure browser app for the cloud GUI (role doc, Tory's
  call).
- Whisper local vs cloud (cost + latency).
- Training pipeline re-source (step 00) from cloud: Litestream restore vs an
  export endpoint.
- Claude Desktop chat (IndexedDB) ingestion: still deferred, unchanged.
