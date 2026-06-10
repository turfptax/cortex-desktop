# Cortex Desktop v0.19.0

**Released:** June 2026
**Theme:** Real software - search-first Hub, semantic recall, phone bridge

The 0.19 cycle promotes 10 dev releases into a stable. It is the "from
prototype to product" release: the Hub got a deliberate redesign (a
search-first front door over the corpus), the desktop learned to run
without the Pi, the phone now talks to the desktop over the cortex-link
dongle, and the engineering floor was raised (CI runs a real test
suite, one config system, connection pooling, a component library).

Code lives across two repos:

- **cortex-desktop** (this repo) - Hub UI + backend + MCP server + daemon
- **[cortex-core](https://github.com/turfptax/cortex-core)** - Pi-side
  overseer plugin, vector index, schema

---

## Headline capabilities

### Search-first Hub (UI redesign phases 1-2a)

The Hub opens on an omnibar: "Ask your corpus anything." Results come
from the new semantic index with similarity scores and `g:` drill
tokens; clicking one opens the full gist with next-token chips
(`q:`/`t:`) for walking the abstraction graph. Navigation reorganized
by what things ARE: **Search / Corpus / Chat / Journal / System /
Settings**. Every section and corpus sub-tab deep-links
(`#/corpus/insights`) and survives refresh; pre-redesign bookmarks
keep working via aliases.

### Semantic search over the corpus (vector index)

Pi-side: sqlite-vec in overseer.db + bge-small embeddings served by a
second llama-server instance (37 MB model, 30 MB RAM, localhost-only -
vectors never leave the host). 3,608 gists embedded, 21-23 ms KNN over
the full corpus, embed-on-write for new gists. Desktop-side: Hub
proxies + the search UI. Paraphrase queries work: "a bracelet that
reads forearm muscle signals" finds the OpenMuscle gists with zero
keyword overlap.

### Phone bridge (cortex-mobile integration)

The Pixel connects to the cortex-link dongle over BLE; the desktop
answers. The `cortex_mcp` daemon now classifies inbound serial lines -
phone-originated `CMD:` requests route to a responder (ping/echo
verified on hardware from both streams) and can never corrupt replies
to the daemon's own MCP clients on the shared port. Sync contract v2
ratified with the mobile stream (uuid idempotency, opaque cursors,
Gateway-preferred transport). The Pi is now OPTIONAL: an empty
`pi_host` means dongle/Gateway-only operation with fast, clean errors
instead of timeouts.

### Pet purge

The pet lives in the cortex-pet repo; the desktop dropped all of it:
sidebar widget + display emulator, three Pi sub-tabs, 18 backend
routes, 5 MCP tools, and the pet-persona chat template system. The
dream cycle (training trigger) survives standalone. MCP surface:
57 -> 52 tools, the first concrete step of the planned collapse.

---

## Engineering floor

- **CI runs tests.** 58-test suite (was: tests existed, CI never ran
  them) gates every build and release tag.
- **One config system.** `%APPDATA%/Cortex/config.json` is the single
  source of truth with env > config.json > default precedence for the
  Hub backend AND the MCP server. A Pi IP change in Settings reaches
  everything without restarts.
- **Backend spine.** FastAPI lifespan (deprecated on_event hooks
  gone), one pooled httpx client to the Pi (was client-per-request
  under constant polling), pydantic v2 sweep.
- **Frontend foundation.** TanStack Query for server state, a ui
  component library (Button/Card/Badge on the theme tokens), a root
  error boundary, and OverseerPage split from a 4,936-line god
  component into 8 focused modules.
- **Daemon hardening.** Fixed a token-clobber bug where any failed
  second daemon start locked all TCP clients out of the live daemon.

## Fixes of note

- `query` MCP tool: filters on underscore columns (`note_type`,
  `session_id`) were silently dropped, returning unfiltered rows.
- Overseer notes high-water mark could stall and rescan the same
  window every tick.
- File uploads no longer report success when DB registration failed.

## Upgrade notes

- Old hashes redirect (`#/overseer` -> Corpus, `#/pi` -> System).
- Chat presets: pet persona presets were removed; an active
  "Tamagotchi" preset migrates to "Default" automatically.
- To run without a Pi, set `"pi_host": ""` in config.json.
