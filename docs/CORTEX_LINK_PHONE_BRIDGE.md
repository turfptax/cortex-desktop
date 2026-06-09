# Cortex Link phone bridge — what changed and what cortex-desktop needs to do

**Date:** 2026-06-09 (evening). **Status:** the bridge is LIVE and verified.
**From:** the cortex-mobile/gateway work stream. Questions: ask Tory, or read
the linked code — every claim here was verified against running hardware today.

## TL;DR

Tory's phone (Pixel 10a, cortex-mobile app) now connects DIRECTLY to the
cortex-link ESP32-S3 dongle over BLE and exchanges Cortex protocol lines with
the desktop through the dongle's USB serial port. Verified end-to-end today:
phone `CMD:ping` -> BLE -> dongle -> USB-CDC -> desktop -> `RSP:ping` -> back,
connection holding indefinitely, cable-free.

This changes cortex-desktop's role: it is no longer only a CLIENT that sends
commands up the serial port (to a Pi on the far BLE side). **The far BLE side
is now (usually) the PHONE, and the phone originates requests — the desktop
must ANSWER inbound `CMD:` lines arriving on the dongle's serial port.**

Two asks:

1. **Don't hard-require the Pi's local IP.** The Pi (.25) may be off or
   demoted (Azure pivot — the cloud corpus is the Azure Gateway now, see
   `cortex-gateway` repo). Treat transports as: Azure Gateway URL (cloud),
   local Pi IP (optional, may be absent), and the dongle serial port
   (the phone bridge). The app should run fine with ONLY the dongle.
2. **Add an inbound-command responder on the dongle serial port.** The
   `cortex_mcp` daemon already owns the COM port — that is the natural home.
   Lines arriving that are `CMD:*` (not `RSP:/ACK:/ERR:` replies to your own
   outstanding requests) are phone-originated requests: handle and reply.

## Topology now

```
cortex-desktop  ⇄ USB-CDC serial ⇄  cortex-link (ESP32-S3)  ⇄ BLE ⇄  phone (cortex-mobile)
     │                                                                    │
     └── HTTPS ──► Azure Gateway (cloud corpus) ◄── optional cloud link ──┘
```

The Pi can still be a BLE peer when the phone isn't connected (the dongle
serves whoever connects), but only ONE central at a time — coexistence
policy is an open design question.

## Protocol contract (verified today)

- Newline-delimited lines, both directions: `CMD:<command>[:<json>]`,
  `RSP:<command>:<json>`, `ACK:<command>:<detail>`, `ERR:<command>:<msg>`.
- The dongle's `serial_bridge.py` relays serial⇄BLE transparently with these
  rules you must respect on the serial side:
  - Single BLE notification ≤ ~200 bytes. **Any serial line you write longer
    than ~199 bytes is auto-split by the dongle** into `CHUNK:n/N:<≤180B>\n`
    frames (the phone reassembles; 15s reassembly timeout).
  - Inbound from the phone, chunked messages are reassembled BY THE DONGLE
    before being written to your serial port — you receive whole lines.
  - Lines starting `TOOL:` are display-only on the dongle (not forwarded).
- The dongle also prints its own debug lines to the same serial stream
  (`BLE: advertising...`, `BLE: connected...`, `Bridge: ...`, `main.py: ...`).
  **Your serial reader must tolerate non-protocol lines** — log and skip.

## Commands to support (phase 1)

| Inbound from phone | Reply | Notes |
|---|---|---|
| `CMD:ping` | `RSP:ping:{"ok": true, "host": "desktop", ...}` | The app sends this automatically on connect; its UI shows "bridge to desktop is alive" on reply. |
| `CMD:echo:<json>` | `RSP:echo:<same json>` | Test plumbing. |
| (proposed) `CMD:sync_push:{"kind": "human_journal", "rows": [...]}` | `RSP:sync_push:{"ok": true, "accepted": N}` | Phone uploads voice journals/notes captured offline. Contract to be finalized WITH the mobile stream before building — ping us. |
| (proposed) `CMD:sync_pull:{"since": "<iso>", "kinds": ["gist"]}` | `RSP:sync_pull:{"rows": [...]}` (auto-chunked by the dongle) | Phone pulls fresh gists. Keep payloads small (BLE ≈ 8-10 KB/s); paginate. |

A reference responder (what answered today's pings) lives at
`cortex-link/tools/serial_ping_responder.py` — productionize that behavior
inside the daemon rather than running it standalone (one process per COM port).

## Hardware + driver facts (hard-won today)

- **Identify the dongle by USB `VID_303A`** (Espressif), never by COM number
  or "USB Serial Device" description. Concrete trap: Tory's TourBox enumerates
  as `USB Serial Device (COM3)`; the dongle was COM5.
- ESP32-S3 does a normal **enumerate-bounce** on plug-in (bootloader → app
  USB). One disconnect/reconnect right after plugging = healthy, not flapping.
- USB-CDC ignores baud; pyserial still requires one (115200 fine). Avoid
  toggling DTR/RTS on open where possible.
- Firmware deploys with `mpremote connect <COM> fs cp <file> :<file>` +
  `mpremote connect <COM> reset` — seconds, no toolchain. Firmware stays
  MicroPython (decision 2026-06-09): BLE pacing (~8-10 KB/s) is the bottleneck,
  not CPU; bulk transfers belong on USB/WiFi/Gateway anyway.

## Bugs already found and fixed — don't reintroduce

1. **BLE bonding is now DISABLED in firmware** (cortex-link commit `cdd6482`).
   `bond=True` invited Android Settings "pairing", which corrupts on reconnect
   (stale-bond "reboot or forget" errors) and dropped unbonded links
   mid-negotiation. This is an app-driven GATT pipe; no SMP, no pairing, ever.
   If you reflash a dongle with old firmware, redeploy `ble_server.py`.
2. **react-native-ble-plx `connect({timeout})` is a footgun** (cortex-mobile
   commit `10b41f2`): on Android it kills LIVE connections N ms after the
   call (we measured deterministic 19s drops). Relevant if anyone builds
   another BLE central. Guard the attempt externally; never pass the option.

## Code pointers

- Phone BLE client: `cortex-mobile/src/link/ble.ts` (scan by service UUID
  `a0e1b2c3-d4e5-f6a7-b8c9-0a1b2c3d4e50`; TX notify `…4e51`, RX write `…4e52`).
- Dongle firmware: `cortex-link/ble_server.py` + `serial_bridge.py`.
- Desktop serial ownership today: `cortex-desktop/cortex_mcp/daemon.py`.
- Cloud corpus: `cortex-gateway` repo (Azure App Service + Azure SQL,
  `https://cortex-gw-8fed.azurewebsites.net`) — the phone's optional cloud
  link and the AI-connector MCP both live there.
