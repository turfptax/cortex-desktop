# Team mail — async messages between the Cortex work streams

Two work streams are building in parallel:

- **desktop** — the cortex-desktop rework (Hub, daemon, training)
- **mobile** — cortex-mobile (the phone app) + cortex-gateway (Azure) +
  cortex-link (the ESP32 bridge firmware)

This folder is the mailbox between them. Plain markdown, committed to git, so
every AI/human session sees it without needing the other stream online.

## Convention

- [`TO_DESKTOP.md`](TO_DESKTOP.md) — mobile stream writes here; desktop reads.
- [`TO_MOBILE.md`](TO_MOBILE.md) — desktop stream writes here; mobile reads.
- Append new entries AT THE TOP, dated, with a status line:

```markdown
## 2026-06-09 — <one-line subject>
**Status:** open | answered (see <link>) | done
<body — keep it self-contained; link code/commits/docs rather than restating>
```

- When you act on an entry, flip its **Status** and link your reply/commit.
- Check your inbox at the START of a work session, and leave mail INSTEAD OF
  blocking on the other stream. Contracts that need both sides (e.g. sync
  message shapes) get drafted in one entry and confirmed in a reply entry
  before either side builds.
