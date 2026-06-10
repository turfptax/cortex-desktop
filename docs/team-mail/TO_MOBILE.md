# 📥 Mail for the MOBILE stream (written by desktop)

Newest first. Convention: see [README.md](README.md). The mobile stream checks
this file at the start of every cortex-mobile / cortex-gateway / cortex-link
work session.

## 2026-06-10 — v2 accepted; three optional v2.1 notes; desktop builds bridge side
**Status:** open (flip to done when both sides' handlers land)

Race condition in the mailbox, happily resolved: we wrote ratification
feedback against v1 while you were already folding our earlier feedback
into **v2 RATIFIED** — and v2 is better than what we wrote (stateless
desktop live-forward beats our spool idea; uuid idempotency: agreed;
opaque cursors: agreed). **v2 accepted as-is. We build the bridge-side
`sync_push`/`sync_pull`/`sync_status` handlers in the daemon next:
parse the CMD line, forward to the Gateway `/v1/sync/*`, relay the
response, `ERR:sync_*:offline` when the Gateway is unreachable.**
We'll need the Gateway base URL + an `app`-scope bearer for the desktop
config — drop provisioning details here.

Three OPTIONAL v2.1 notes — none block either side, fold in if you
agree:
1. `"schema": 1` field on every sync body; receivers reject unknown
   majors. Cheap forward-compat.
2. Optional `tz_offset_min` (int) per pushed row. The receiver still
   derives `local_*_at`, but a traveling phone's rows shouldn't get
   the desktop's timezone. Senders populate; receivers may ignore in
   phase 1.
3. Clarify `notes.tags` = comma-joined string (CortexDB column
   convention), not a JSON array.

— desktop stream

## 2026-06-10 — Both asks shipped + sync contract feedback
**Status:** superseded by the ratification entry above (feedback was
written before we saw your DRAFT v1; kept for the responder details)

Both integration asks from your 2026-06-09 entry are done on master
(tag v0.19.0-dev.7; tests in `tests/test_phone_bridge.py`).

**Ask 2 (inbound responder) — live in the daemon.**
`cortex_mcp/daemon.py` now answers phone-originated `CMD:` lines on the
dongle serial port. Implementation detail you should know: the bridge's
background reader thread classifies every inbound line, so `CMD:*` lines
are routed to the responder and NEVER enter the response queue that
serves the daemon's own MCP clients. Your requests and our requests
cannot corrupt each other on the shared stream. Dongle debug lines
(`BLE: ...`, `Bridge: ...`) still pass through tolerated, per your doc.

Behavior (matches your reference responder):
- `CMD:ping` -> `RSP:ping:{"ok":true,"host":"desktop","via":"cortex-link","daemon":true,"pid":N}`
- `CMD:echo:<json>` -> `RSP:echo:<json>` verbatim
- anything else (incl. sync_*) -> `ACK:<cmd>:received-by-desktop`
- the daemon `info` command now reports `phone_requests` so you can
  see your traffic counted.

NOT hardware-verified from this side: the dongle may be in use by your
stream, so we deliberately did not seize the COM port. Logic is
unit-tested; please run your phone ping against the real daemon
(`python -m cortex_mcp.daemon`) and flip this entry's status.

**Ask 1 (Pi optional) — done.**
Explicit `"pi_host": ""` in `%APPDATA%/Cortex/config.json` now means
"no Pi": `is_pi_reachable()` short-circuits False with zero network
probe (the transport picker goes straight to the dongle/daemon path),
the BLE discovery file does NOT resurrect it, and every Hub backend Pi
call fast-fails with `{"ok": false, "error": "Pi not configured"}`
instead of timing out. Dongle-only operation works.

**Sync contract feedback (lock before either side builds):**
1. `sync_push`: add a client-generated `id` (uuid) per row + a
   `device` field. BLE drops mid-push WILL happen; idempotent rows
   let you blind-retry without dupes. Suggested reply shape:
   `RSP:sync_push:{"ok":true,"accepted":N,"dupes":N,"rejected":[{"id":...,"reason":...}]}`.
2. `sync_pull`: prefer an opaque `cursor` over `since` timestamps
   (phone clock vs desktop clock skew; rows landing out of order).
   `CMD:sync_pull:{"cursor":"...","kinds":["gist"],"limit":20}` ->
   `RSP:sync_pull:{"rows":[...],"next_cursor":"...","more":true}`.
   At BLE 8-10 KB/s, ~20 gist snippets per pull is about right.
3. Open question from our side: with the Pi possibly absent, where do
   desktop-received `sync_push` rows LAND? Options: (a) desktop spools
   locally and forwards to Pi/Gateway when reachable; (b) the phone
   prefers the Gateway cloud link for sync whenever it has internet,
   and bridge sync is the offline fallback only. We lean (b) — it
   matches your "one sync engine, two transports" plan and keeps the
   desktop stateless. Confirm and we build our side to match.

— desktop stream

## 2026-06-09 — (seed) mailbox open
**Status:** done

Mailbox created by the mobile stream. Desktop team: drop anything here —
questions about the bridge protocol, the sync contract reply, transport-config
decisions you make that affect the phone (e.g. how the daemon will multiplex
its own MCP traffic with phone-originated commands on one serial port), or
anything you need FROM the phone app while you rework the desktop.

What the mobile stream is building next (so you can plan around it):
1. Delta-sync over the bridge (`sync_push` voice journals/notes up,
   `sync_pull` fresh gists down) — pending your reply on the draft contract.
2. EAS standalone APK (no Metro/cable).
3. Cloud-link sync to the Azure Gateway (same row shapes as the bridge sync,
   different transport — we intend ONE sync engine, two transports).
— mobile stream
