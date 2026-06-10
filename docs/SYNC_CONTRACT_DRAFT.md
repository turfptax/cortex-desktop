# Cortex sync contract — v2 RATIFIED 2026-06-10

**Author:** mobile stream; **amendments:** desktop stream (TO_MOBILE.md
2026-06-10), all accepted. **Status: RATIFIED — both sides may build.**

v1→v2 changes (desktop amendments):
1. Every pushed row carries a client-generated **uuid `id`** (+ batch-level
   `device`); idempotency key is the uuid, enabling blind retries after BLE
   drops. Reply shape gains `dupes` + `rejected`.
2. Pulls use an **opaque `cursor`** (receiver-defined), not `since` timestamps
   — immune to phone/desktop clock skew and out-of-order row arrival.
3. **Transport policy (b):** the phone prefers the **Azure Gateway** for sync
   whenever it has internet; the BLE bridge is the offline fallback. Over the
   bridge, the desktop stays **stateless**: it live-forwards sync messages to
   the Gateway and relays the response; if the desktop is offline too, it
   returns `ERR:sync_*:offline` and the phone keeps rows queued locally.

## Scope and principles

Moves data between the phone's on-device SQLite and the canonical corpus.

1. **One engine, two transports.** The same messages run over BOTH the
   cortex-link BLE bridge (as `CMD:/RSP:` lines, see
   [CORTEX_LINK_PHONE_BRIDGE.md](CORTEX_LINK_PHONE_BRIDGE.md)) and the Azure
   Gateway (as HTTPS POSTs to `/v1/sync/push` + `/v1/sync/pull` with the same
   JSON bodies). Build a transport-agnostic handler.
2. **Append-only in phase 1.** Synced kinds are immutable events (journal
   entries, notes, gists). No edits, no deletes, therefore no conflict
   resolution yet — the Harbor-Notes conflict question stays deferred.
3. **Idempotent.** Every pushed row carries a client-generated uuid `id` (and
   the batch a `device`); the receiver dedupes on the uuid (`sync_row_map`:
   uuid → canonical id, kept in the canonical DB). Blind replays after
   transport drops are always safe.
4. **Small frames.** Over BLE, one message line should stay ≤ ~4 KB before
   the dongle's chunking (it can carry more, but pacing is ~8-10 KB/s — keep
   interactions snappy). Default page size 10, max 50.
5. **Timestamps** are `"YYYY-MM-DD HH:MM:SS"` UTC (SQLite convention). The
   receiver derives `local_*_at` per the locked UTC+local rule — senders never
   compute local time for the receiver.

## Phase-1 kinds

| Kind | Direction | Row fields |
|---|---|---|
| `human_journal_entries` | phone → corpus | `local_id, text, entry_type, created_at` |
| `notes` | phone → corpus | `local_id, content, note_type, project, tags, created_at` |
| `summaries_gist` | corpus → phone | `id, period_label, body, confidence, created_at` |
| `temporal_narratives` | corpus → phone | `id, kind, period_label, period_start, period_end, narrative, created_at` |

## Messages

### Push (phone → desktop/cloud)

```
CMD:sync_push:{"device":"pixel-10a","kind":"human_journal_entries","rows":[
  {"id":"5f1c…uuid…","local_id":15,"text":"…","entry_type":"voice",
   "created_at":"2026-06-09 23:13:56"}]}

RSP:sync_push:{"ok":true,"kind":"human_journal_entries","accepted":1,
  "dupes":0,"rejected":[],"ids":{"5f1c…":402}}
```
- `id` = client-generated uuid per row (the idempotency key); `local_id` is
  informational for the phone's own bookkeeping. `ids` maps uuid → canonical
  row id; the phone records it in `synced(kind, uuid, local_id, remote_id,
  synced_at)`. Re-pushing any row is harmless.
- `rejected`: `[{"id": "<uuid>", "reason": "<msg>"}]` — per-row failures don't
  fail the batch.
- `ERR:sync_push:offline` — bridge transport only: desktop has no Gateway
  reach; phone keeps rows queued.

### Pull (desktop/cloud → phone)

```
CMD:sync_pull:{"device":"pixel-10a","kind":"summaries_gist",
  "cursor":"", "limit":10}

RSP:sync_pull:{"ok":true,"kind":"summaries_gist","rows":[…],
  "more":true,"next_cursor":"g:3617"}
```
- `cursor` is OPAQUE to the phone: send `""` for "from the beginning", then
  echo back `next_cursor` verbatim. The receiver defines its meaning
  (recommended: `<kind-prefix>:<last-id>` — monotonic ids, no clock skew).
- The phone inserts pulled rows keeping the CANONICAL id (its interpretive
  tables are read-only mirrors; phone-local ids only exist for phone-authored
  rows).

### Status (either side, cheap)

```
CMD:sync_status:{"device":"pixel-10a"}
RSP:sync_status:{"ok":true,"counts":{"summaries_gist":3607,…},
  "newest":{"summaries_gist":"2026-06-09 19:47:07",…}}
```
Lets the phone show "corpus is N rows / newest X" and decide whether to pull.

## Transport mapping

| | BLE bridge | Azure Gateway |
|---|---|---|
| push | `CMD:sync_push:<json>` line | `POST /v1/sync/push` body `<json>`, bearer `app` scope |
| pull | `CMD:sync_pull:<json>` | `POST /v1/sync/pull` |
| status | `CMD:sync_status:<json>` | `GET /v1/sync/status?device=` |

Desktop answers the BLE side (daemon serial handler); the Gateway answers the
HTTPS side (mobile stream builds that). Both ultimately write/read the same
canonical store, so the `sync_row_map` dedup table must live in the canonical
DB, not per-transport.

## Resolved questions (v2)

1. **Where do bridge-pushed rows land?** Policy (b), desktop-proposed,
   mobile-confirmed: phone prefers the Gateway when online; over the bridge
   the desktop live-forwards to the Gateway (stateless) and relays the
   response, or returns `ERR:sync_*:offline`. No desktop spooling.
2. **Serial multiplexing:** desktop's daemon classifies inbound lines —
   phone-originated `CMD:`s are routed to the responder and never enter the
   MCP response queue (shipped in v0.19.0-dev.7). Resolved.
3. Row shapes: desktop added uuid `id` + `device` (v2 §Push). No further asks.
