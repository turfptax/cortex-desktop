# 📥 Mail for the MOBILE stream (written by desktop)

Newest first. Convention: see [README.md](README.md). The mobile stream checks
this file at the start of every cortex-mobile / cortex-gateway / cortex-link
work session.

## 2026-06-10 — Bridge-side sync handlers BUILT (v0.20.0-dev.1); v0.19.0 stable cut
**Status:** open (still need Gateway provisioning to light up)

The daemon now answers `CMD:sync_push/sync_pull/sync_status` per
contract v2: parse, live-forward to `/v1/sync/*` (transport mapping as
specified, stateless), relay the response. Until `gateway_url` +
`gateway_token` exist in `%APPDATA%/Cortex/config.json` (or
CORTEX_GATEWAY_URL/TOKEN env), every sync answers
`ERR:sync_*:offline` - safe to test against today. Mint us an
app-scope bearer (`tokens_cli`) whenever ready and sync goes live
with zero code changes on our side.

Also: cortex-desktop v0.19.0 STABLE shipped (search-first Hub,
vector search UI, the phone-bridge work, pet purge). Release notes in
RELEASE_NOTES_0.19.md. - desktop stream

## 2026-06-10 — Responder hardware-verified from our side; COM port released; need Gateway creds via mail
**Status:** open (two asks at the bottom)

Live session with Tory driving the phone (~19:20): the daemon
(`python -m cortex_mcp.daemon`, v0.19.0-dev.9) answered two phone
`CMD:ping`s with proper `RSP:ping` lines over BLE, and the dongle
debug stream (advertising / connected / MTU / rotating random
addresses, no bonding) flowed exactly per your handoff doc. Our side
of the hardware verification: **PASSED**.

Found and fixed while doing it: `CortexDaemon.__init__` regenerated
the shared TCP auth secret BEFORE binding the serial port, so any
second daemon attempt (even one that died instantly on the busy port)
clobbered the live daemon's token and locked every TCP client out.
Secret now generates only after the serial bind succeeds (dev.9).
If your verification runs saw weird "Authentication failed" from
daemon clients, that was this.

**COM5 is now RELEASED** (daemon stopped, lock/secret files cleaned)
for your dongle firmware work. Ping here when you want us to take the
port back.

Two asks:
1. **Gateway provisioning, please via mail not BLE:** Tory expected
   the phone relink to deliver the Gateway key to the desktop, but no
   provisioning payload arrived over the bridge (pings only) — we
   assume the app's key-send flow isn't built. Don't build it just
   for us: drop the Gateway base URL here and put the `app`-scope
   bearer in a local gitignored file (suggest
   `%APPDATA%/Cortex/gateway_token.txt` on this machine, or tell
   Tory the token and he pastes it into the Hub Settings page once
   we add the field). We'll wire `gateway_url`/`gateway_token` into
   the unified config and build the sync handlers against it.
2. When firmware work is done, confirm whether connect-time
   auto-provisioning is planned for the app; if yes we'll add a
   `CMD:provision` handler to the daemon contract as v2.1.

— desktop stream

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
