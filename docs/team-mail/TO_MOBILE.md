# 📥 Mail for the MOBILE stream (written by desktop)

Newest first. Convention: see [README.md](README.md). The mobile stream checks
this file at the start of every cortex-mobile / cortex-gateway / cortex-link
work session.

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
