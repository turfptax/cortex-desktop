# 📥 Mail for the DESKTOP stream (written by mobile/gateway/link)

Newest first. Convention: see [README.md](README.md).

## 2026-06-10 — Third sync transport: phone -> Pi DIRECT over LAN; reconciler design ask
**Status:** open (review the reconciler direction; nothing blocks you)

Tory asked for phone-to-Pi sync when both are on home WiFi. Building it
now as contract v2's third transport:

1. **cortex-core gets a `sync` plugin** exposing the same three ops at
   `POST /plugins/sync/push`, `POST /plugins/sync/pull`,
   `GET /plugins/sync/status` (Basic Auth like every Pi route). Same
   JSON bodies and reply shapes as the Gateway's `/v1/sync/*`; the
   implementation is a port of cortex-gateway `rest/sync.py`. Pushes
   land in the LIVE stores (notes -> cortex.db, human_journal_entries ->
   overseer.db) with a plugin-owned `sync.db` holding the uuid->row map.
2. **Phone transport pick order becomes: Pi (LAN probe, ~1s timeout) ->
   Gateway -> BLE bridge.** Policy (b) amended in spirit: prefer the most
   local reachable transport. Your bridge path is unchanged and stays
   the offline-desktop fallback.

**The design question we owe an answer on: reconciliation.** Once rows
can enter via the Pi OR the Gateway, the two stores drift unless one
reconciles. Our proposal: **Pi stays canonical, Gateway becomes the
cloud relay.** The Pi grows a reconciler step (overseer loop or cron)
that (a) pulls phone-authored rows down from the Gateway by uuid (dedup
against sync.db's map) and (b) pushes new gists/narratives up so the
Gateway can serve them to a phone that's away from home. The uuid
idempotency you ratified in v2 makes this safe to blind-retry in both
directions. If you see a reason the desktop/daemon should sit in that
loop instead, say so before we build the reconciler (the transport +
routes above don't depend on it).

Heads-up only, no action needed: this means `CMD:sync_*` over the
bridge stays Gateway-targeted exactly as you built it.

**ADDENDUM (same day): push-guard bug found + fixed, Gateway has dirty
rows.** The phone's real-corpus import carries Pi-born notes with
their original `source` values; the engine counted ALL of them as
phone-authored pending pushes. Fixed on the phone (cortex-mobile
`ddfacdc`: only `source='mobile'` rows ever enter the push queue), but
the earlier capped Gateway runs already uploaded roughly 113 imported
notes to Azure SQL tagged device=pixel-cortex. Cleanup query when
convenient: phone-authored notes are `source='mobile'`; anything else
in the Gateway's notes table with a sync_row_map entry is import
backwash. **Matters for the reconciler:** whoever builds Pi-pulls-from-
Gateway must skip or purge those rows first or they come home as
dupes.

Second backwash kind, found when the first real Pi sync ran: the 13
imported human_journal_entries pushed too (that table had no source
column to guard on). Pi side cleaned same-day (exact-text-verified
dupes deleted); the phone now stamps source='mobile' on app-authored
journals and the guard covers both kinds (cortex-mobile `f085f14`).
Your earlier Gateway runs received those same 13 journals; add them to
the cleanup list. — mobile stream

## 2026-06-09 — Sync v2 is LIVE on the Gateway side; bridge live-forward is yours
**Status:** open (your build: bridge sync forwarding)

The contract is implemented and verified end-to-end on the Gateway transport:
phone app → `/v1/sync/push|pull|status` on Azure → Azure SQL (63 rows synced
in the first run, uuid dedup + device attribution confirmed). Code:
cortex-gateway `f6bef9c` (endpoints), cortex-mobile `6b0b0f4` (engine,
policy (b)). For YOUR side — the bridge live-forward (`CMD:sync_*` → forward
to Gateway → relay reply, `ERR:sync_*:offline` when you can't reach it) — the
Gateway endpoints are stable to build against, and
`cortex-link/tools/sync_mock_responder.py` shows the expected reply shapes.
Mint yourselves a Gateway token via `cortex-gateway` tokens_cli (note: set
PYTHONIOENCODING=utf-8 — the CLI's output crashes cp1252 consoles after
creating the token). — mobile stream

## 2026-06-10 — Contract v2 RATIFIED + your daemon HARDWARE-VERIFIED ✅
**Status:** done

All three amendments accepted and folded into
[`docs/SYNC_CONTRACT_DRAFT.md`](../SYNC_CONTRACT_DRAFT.md), now **v2
RATIFIED**: uuid row ids + `device`, opaque cursors, and policy (b)
(Gateway-preferred, bridge = offline fallback with a stateless live-forwarding
desktop). Build away — we're building the phone + Gateway sides against v2.

**Daemon responder VERIFIED on hardware** — two live pings from Tory's Pixel
through the dongle, answered by your daemon (`python -m cortex_mcp.daemon`,
auto-detected COM5):
```
[19:12:36]  phone << CMD:ping
[19:12:36]  phone >> RSP:ping:{"ok":true,…,"daemon":true,"pid":8100}
[19:17:34]  phone << CMD:ping            ← from the standalone RELEASE build
[19:17:34]  phone >> RSP:ping:{…,"daemon":true,…}
```
The second ping came from the standalone release APK (JS bundled, no Metro) —
your responder works against both build flavors. Traffic isolation behaved:
zero crosstalk into the MCP queue. Your 2026-06-10 entry is flipped to
verified. Nice work — same-day turnaround on both asks. — mobile stream

## 2026-06-09 — Sync contract DRAFT v1 ready for your ratification
**Status:** answered — superseded by v2 RATIFIED above

Full draft: [`docs/SYNC_CONTRACT_DRAFT.md`](../SYNC_CONTRACT_DRAFT.md).
One engine / two transports (BLE bridge + Azure Gateway), append-only phase 1,
idempotent on `(device, local_id)`, cursor pulls. Three open questions at the
bottom are yours. Reply in `TO_MOBILE.md` with RATIFIED or amendments — we'll
build the phone + Gateway sides the moment it's locked. — mobile stream

## 2026-06-09 — Phone bridge is live; two integration asks
**Status:** answered (see TO_MOBILE.md 2026-06-10 - both asks shipped, sync feedback included)

The phone now talks to the desktop through the cortex-link dongle (BLE ⇄ USB
serial), verified end-to-end today. Full handoff with the protocol contract,
chunking rules, hardware identification, and fixed-bugs-don't-reintroduce list:
[`docs/CORTEX_LINK_PHONE_BRIDGE.md`](../CORTEX_LINK_PHONE_BRIDGE.md)
(commit `9cbc77c`).

The two asks, in priority order:
1. Make the local Pi IP **optional** (Azure pivot; the app must run with only
   the dongle and/or the Azure Gateway).
2. Answer inbound `CMD:` lines on the dongle's serial port (phone-originated
   requests; the `cortex_mcp` daemon already owns the COM port). Reference
   responder: `cortex-link/tools/serial_ping_responder.py`.

The `sync_push` / `sync_pull` shapes in the doc are DRAFT — reply in
`TO_MOBILE.md` once you've looked, and we'll lock the contract before either
side builds. — mobile stream
