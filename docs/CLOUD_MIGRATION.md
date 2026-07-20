# Cloud migration: cortex-desktop's role 2026-07-14

> Canonical direction + dependency order + all-team roles:
> `cortex-core/docs/CLOUD_MIGRATION.md`. Full plan (diagram, phases, cost): the
> "Solo cloud migration" artifact (ask Tory). Read that first; this is the
> desktop-specific slice.

## The shift, in one line

Cortex moves off the home Pi into one Azure Container App per person. The
desktop **splits in two**: the GUI moves to the cloud, and a small local agent
stays behind to ingest the files that only exist on this machine.

## What retires

- **The Pi-proxy Hub backend.** The FastAPI backend currently proxies to the Pi
  at `10.0.0.25`. In the cloud model there is no Pi; the source of truth is the
  cloud core, reached through the cloud gateway over OAuth.
- **The sync live-forward daemon.** It currently forwards the Pi's changes to the
  Azure SQL gateway mirror (contract v2). That mirror goes away (the gateway
  reads the core's SQLite directly), so the daemon's forwarding job goes away too.

## What moves to the cloud

- **The Hub GUI.** The React SPA is served from the cloud (visit your cloud URL),
  or the tray app repoints its webview at the cloud gateway. Either way the Hub
  reads/writes the corpus through the cloud gateway's `/v1` + overseer routes over
  OAuth, not a local Pi proxy. Decision to confirm with Tory: keep a thin tray
  launcher, or make the Hub a pure browser app at the cloud URL.

## What stays LOCAL (the desktop's durable reason to exist)

- **A lightweight Claude-file ingester.** Claude Code and Claude Desktop write
  conversation `.jsonl` files on THIS machine; only a local process can see them.
  Keep a small agent that watches the projects dir and pushes new/changed
  sessions to the cloud gateway's ingest endpoint over OAuth (the loopback OAuth
  flow the gateway already supports, `GATEWAY_OAUTH_ALLOW_LOOPBACK`). This is the
  same import the overseer loop then gists in the cloud.
- **Optionally local whisper.cpp** for transcribing audio/video files that live on
  the machine, pushing the transcript up. (Or move transcription to the cloud;
  TBD by cost + latency.)

## Auth

Adopt the OAuth 2.1 + PKCE loopback flow (the phone's flow, gateway-side already
built) so the local ingester and any tray launcher authenticate as the owner.
Store the token in the Windows profile (DPAPI), never the browser.

## Sequencing

GATED on the cloud app existing (core P3 in the canonical doc). Until then, keep
the current Pi-proxy behavior working. When the cloud app is up: (1) repoint the
Hub at the cloud, (2) ship the local ingester, (3) retire the proxy backend + the
live-forward daemon, (4) verify a full day cloud-only with the Pi off.
