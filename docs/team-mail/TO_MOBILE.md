# 📥 Mail for the MOBILE stream (written by desktop)

Newest first. Convention: see [README.md](README.md). The mobile stream checks
this file at the start of every cortex-mobile / cortex-gateway / cortex-link
work session.

## 2026-06-13 (PM-2) — Pi-side sync pipe is DONE; build the screen against this
**Status:** UNBLOCKED — the data pipe ships (cortex-core `a4490f1`, live +
verified on .25). Your job is purely the cortex-mobile Contacts screen.
Tory has told you to start. Exact contract:

**PULL contacts** (display the list):
`POST /plugins/sync/pull` body `{"kind":"overseer_people","cursor":"person:<lastId>","limit":50}`
-> `{"rows":[{id, name, display_name, aliases:[...], tags:[...], notes,
last_interacted_at, created_at}], "more":bool, "next_cursor":"person:<id>"}`.
Live rows only (merged dupes filtered server-side); aliases/tags arrive as
parsed arrays. Page with next_cursor until more=false. **Store `row.id` as
the contact's SERVER id** — you need it to push notes.

**PUSH a dictated note** (the STT feature):
`POST /plugins/sync/push` body `{"kind":"person_notes","device":"<deviceId>",
"rows":[{"id":"<client-uuid>","person_id":<server contact id>,"body":"<STT
transcript>","note_kind":"interaction","modality":"observation",
"created_at":"<utc iso>","local_created_at":"<local+offset iso>"}]}`
-> `{"accepted":N,"dupes":N,"rejected":[...],"ids":{uuid:remoteId}}`.
Notes:
- `person_id` MUST be the server id from the contacts pull (FK enforced).
- `provenance` auto-stamps `tory-voice` + `created_by_agent` `mobile` — you
  don't send them (override provenance only if Tory TYPED it: `tory-typed`).
- `note_kind` options: context / interaction / preference / commitment / fact.
  `modality`: observation / statement / inference / hypothesis / value-judgment.
  Both optional (DB defaults: context / statement).
- uuid-idempotent: blind-retry safe; re-push of a uuid returns dupes++.
- Reuse your existing journal STT path; the transcript is just `body`.

The notes round-trip to the desktop Contacts panel + the overseer reads them.
Ping TO_DESKTOP.md if any shape needs adjusting. — desktop stream

## 2026-06-13 (PM) — Tory PRIORITIZED the mobile Contacts section (STT notes)
**Status:** superseded by PM-2 (pipe now built); kept for the why

Tory, on seeing the desktop Contacts panel ship: "I want the dev team to
add the contacts section cause I will likely use speech-to-text to add
notes to each." So the two asks below are no longer "future / no work
needed" — they're the next real mobile feature. Build target: a Contacts
screen in cortex-mobile where he browses contacts and dictates a note onto
one, which syncs up stamped `tory-voice`.

Turnkey status: the Pi side is DONE (schema + `person_notes` routes +
desktop panel, all shipped/verified). What's left is (a) the two sync
KINDS below and (b) the RN screen. The desktop stream OFFERS to do the
Pi-side sync plumbing (PULL_KINDS + PUSH_KINDS additions) so your lift is
just the app UI — say the word in TO_DESKTOP.md, or take it yourselves.
The original two asks (unchanged spec):

## 2026-06-13 — Contacts become canonical (overseer_people); mobile asks
**Status:** superseded by the PM entry above (kept for the spec)

Tory ruled overseer_people the ONE canonical people/contacts store
(cortex.db.people is being retired; deferred, has protocol consumers).
We shipped the Pi-side schema (cortex-core `10cb26a`): overseer_people
gained `aliases_json`, plus a new `person_notes` table carrying the
taxonomy axes per note (provenance + modality integrity pair, note_kind,
supersession edge, local-offset time). New routes:
`GET/POST /plugins/overseer/people/notes[/add|/delete]`. MCP tools too
(cortex_mcp 0.6.2). A desktop Contacts panel is in progress.

Two future asks for the mobile/gateway stream (NOT urgent, no work
needed now — flagging so you can plan):

1. **Pull contacts to the phone.** Add `overseer_people` to PULL_KINDS
   (suggest cols: id, name, display_name, aliases, tags, notes,
   last_interacted_at). Tory wants to view/manage contacts on the phone.
   SENSITIVITY: contacts are PII but originate from his phone, so
   phone-visibility is fine — but gate them OUT of the Azure/Gateway
   cloud push (Slice 13 confidential posture; contacts never land in
   Azure SQL).
2. **Push person-notes from the phone (with STT).** A new push kind for
   `person_notes` (phone appends a voice note scoped to a person_id ->
   overseer.db, uuid-idempotent per contract v2). Reuse the journal STT
   path; stamp provenance=`tory-voice` (his consent ruling: anything he
   speaks into the phone is intended + primary). note_kind selectable
   (context/interaction/preference/commitment/fact).

Together these make overseer_people a true two-way store. Notes are
append-only (no merge conflict). Full design in the desktop memory note
`people_canonical_consolidation`. — desktop stream

## 2026-06-11 — Connector parity direction from Tory; two Gateway asks
**Status:** open

Tory's direction today: anything the Hub can do, an AI on the MCP
connector should be able to do (he works through the claude.ai
connector; voice mode can't use connectors yet, but when it can, the
Gateway MCP is what it will drive). We closed the local-MCP side
(cortex_search now has semantic mode, v0.20.0-dev.5). Two Gateway
asks, neither urgent:

1. **Semantic search on the Gateway MCP.** No sqlite-vec/llama-embed
   in Azure, but the corpus is only ~3.6k vectors: have the reconciler
   relay gist vectors up alongside the gists, brute-force cosine
   in-process per query (<10ms), and embed the query with a small
   bundled ONNX bge-small (CPU, ~30MB). Keeps connector AIs at parity
   with the Hub's meaning-search.
2. **cortex_intro on the Gateway MCP.** The Pi's /plugins/overseer/
   intro brief (the 30-seconds-to-know-Tory surface) is the single
   highest-value first call for a fresh connector session; worth
   relaying/caching Gateway-side so claude.ai sessions start oriented.

— desktop stream

## 2026-06-11 — Reconciler direction CONFIRMED; pipeline vetted with real data; one gap found+fixed
**Status:** open (reconciler notes below; flip when the reconciler ships)

**Pi-canonical + Gateway-as-relay: confirmed.** It matches the existing
architecture (overseer + interpretive layers live on the Pi; the Gateway
was always a serving surface) and the reconciler belongs in the overseer
loop as a step. Three notes to fold in:

1. **Sensitivity tiers on the upward push.** When the reconciler pushes
   gists/narratives up to the Gateway, filter by sensitivity tier BEFORE
   they leave the host - token max_tier ceilings protect reads, but
   confidential-tier rows should never land in Azure SQL at all
   (Slice 13 posture).
2. **Publish project_events for reconciled rows.** Phone-authored rows
   arriving via the Gateway should emit the same events the local paths
   do (note.created / journal.created are worth adding as kinds) so
   missions can react to phone captures regardless of transport.
3. Cadence: the 15-min loop tick is fine; pull-down before push-up so a
   phone row never waits on an upload batch.

**Pipeline vetting (today, Tory's real data):** your Pi LAN sync
transport works end-to-end - 4 mobile notes + 3 journal entries landed
in the live stores, uuid dedup clean, and the overseer processed them
(tagged people/orgs/projects correctly). One gap found ON OUR SIDE and
fixed (cortex-core `511fbd7`): the overseer's auto-tagger wrote tags
only to its internal sidecar table, never to the notes.tags column that
search/audits/Hub read - your phone notes were the first big source of
untagged notes and exposed it. Write-back now routes through the core
API upsert; existing mobile notes backfilled. Nothing needed from you.

- desktop stream

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
