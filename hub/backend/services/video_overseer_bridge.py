"""Overseer bridge — pushes completed cortex-vision sessions to the Pi.

Polls the cortex-vision sidecar every BRIDGE_INTERVAL_S seconds for
sessions that are `status=complete` and `pushed=false`, fetches each
one's hydrated detail, sends the narrative as a note via pi_client,
and POSTs `mark-pushed` only when the Pi push actually succeeded.

The bridge lives in cortex-desktop (not cortex-vision) because pushing
to the Pi requires the existing pi_client. cortex-vision just exposes
the polling-friendly filter (?status=complete&pushed=false) and the
mark-pushed endpoint; this loop owns the rest.

Self-healing:
  - cortex-vision unreachable / 5xx     -> skip tick, retry next
  - Pi unreachable / send_note fails    -> don't mark pushed, retry next
  - One session fails mid-batch         -> continue to the next; the
                                           failed one will appear again
                                           in the next tick's list

Idempotence comes from cortex-vision's mark-pushed: once flipped, the
session no longer appears in the unpushed filter.

Lifecycle is owned by main.py — `start_loop()` returns the asyncio
task; main.py cancels it on shutdown alongside the plugin manager's
health loop.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from services import pi_client
from services.plugin_manager import PluginManager

logger = logging.getLogger("cortex.hub.video_bridge")

PLUGIN_ID = "cortex-vision"
BRIDGE_INTERVAL_S = 30.0
HTTP_TIMEOUT_S = 10.0
BATCH_LIMIT = 20


class VideoOverseerBridge:
    """Async loop that drains cortex-vision's unpushed-session queue."""

    def __init__(self, plugin_manager: PluginManager) -> None:
        self._pm = plugin_manager
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------ #
    # Lifecycle                                                           #
    # ------------------------------------------------------------------ #

    def start(self) -> asyncio.Task:
        """Start the polling loop. Returns the task so main.py can track it."""
        if self._task is not None and not self._task.done():
            return self._task
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run())
        return self._task

    def stop(self) -> None:
        self._stop_event.set()

    # ------------------------------------------------------------------ #
    # Polling loop                                                        #
    # ------------------------------------------------------------------ #

    async def _run(self) -> None:
        logger.info(
            "Video overseer bridge started (interval=%.1fs)",
            BRIDGE_INTERVAL_S,
        )
        try:
            while not self._stop_event.is_set():
                try:
                    await self._tick()
                except Exception as exc:
                    # Never let a tick exception kill the loop.
                    logger.warning("bridge tick failed: %s", exc)
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=BRIDGE_INTERVAL_S
                    )
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            logger.info("Video overseer bridge cancelled")
            raise

    async def _tick(self) -> None:
        plugin = self._pm.get(PLUGIN_ID)
        if plugin is None or not plugin.is_running:
            # cortex-vision isn't installed or isn't running — nothing to do
            return

        base = f"http://{plugin.host}:{plugin.port}"
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as client:
            try:
                resp = await client.get(
                    f"{base}/api/video/sessions",
                    params={
                        "status": "complete",
                        "pushed": "false",
                        "limit": BATCH_LIMIT,
                    },
                )
                resp.raise_for_status()
                entries = resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                logger.debug("bridge poll list failed: %s", exc)
                return

            if not entries:
                return

            # Client-side defensive filter: as of cortex-vision Phase 6
            # the server-side `status=` query param is not actually
            # filtering (returns rows of all statuses). Until that's
            # fixed upstream, drop anything not in "complete" so we
            # don't waste a /sessions/{id} fetch on rows we'd skip.
            # See bridge feedback note in memory.
            ready = [e for e in entries if e.get("status") == "complete"]

            if not ready:
                logger.debug(
                    "Bridge tick: %d unpushed rows but none complete",
                    len(entries),
                )
                return

            logger.info(
                "Bridge found %d complete unpushed session(s) (of %d unpushed)",
                len(ready),
                len(entries),
            )
            for entry in ready:
                session_id = entry.get("id")
                if not session_id:
                    continue
                try:
                    await self._push_one(client, base, session_id)
                except Exception as exc:
                    logger.warning(
                        "Bridge failed for session %s: %s",
                        session_id,
                        exc,
                    )

    async def _push_one(
        self,
        client: httpx.AsyncClient,
        base: str,
        session_id: str,
    ) -> None:
        # 1. Fetch the hydrated session (scenes, narrative, transcript)
        resp = await client.get(f"{base}/api/video/sessions/{session_id}")
        resp.raise_for_status()
        session = resp.json()

        # 2. Send to the Pi via the existing note channel.
        pushed = await self._send_to_pi(session)
        if not pushed:
            # Pi call failed; leave pushed=false so we retry next tick.
            return

        # 3. Flip the flag on cortex-vision side. If THIS fails, we'll
        #    re-push next tick — pi_client.send_note is idempotent over
        #    duplicate notes (same content + timestamp from the session)
        #    only in the soft sense. Acceptable cost vs losing the push.
        try:
            mark = await client.post(
                f"{base}/api/video/sessions/{session_id}/mark-pushed"
            )
            mark.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning(
                "Pushed session %s but failed to mark-pushed: %s",
                session_id,
                exc,
            )

    async def _send_to_pi(self, session: dict[str, Any]) -> bool:
        """Convert one cortex-vision session into a Pi note. Returns True
        only if the note actually landed."""
        narrative = session.get("narrative")
        if not narrative:
            # Pipeline marked complete but no narrative — usually means
            # describer/narrative was skipped (no LLM available). Skip
            # pushing rather than send an empty note.
            return False

        mode = session.get("mode", "file")
        scene_count = len(session.get("scenes", []))
        source_url = ""
        src = session.get("source") or {}
        if isinstance(src, dict):
            source_url = (
                src.get("url")
                or src.get("filename")
                or src.get("device", "")
            )

        # Compose tags. Keep them stable + filterable: the overseer's
        # search hits these directly.
        tag_parts = ["video", f"mode:{mode}", f"session:{session.get('id', '')}"]
        if scene_count:
            tag_parts.append(f"scenes:{scene_count}")
        tags = ",".join(tag_parts)

        project_id = session.get("project_id") or ""
        note_type = f"video-{mode}"  # video-file | video-journal | video-live

        # Prefix the narrative with a tiny header so the overseer / chat
        # surfaces have context — this mirrors the established pattern
        # for transcribe.py outputs.
        header_lines = [f"[video · {mode}] {scene_count} scene(s)"]
        if source_url:
            header_lines.append(f"source: {source_url}")
        content = "\n".join(header_lines) + "\n\n" + narrative

        try:
            await pi_client.send_note(
                content=content,
                tags=tags,
                project=project_id,
                note_type=note_type,
            )
            logger.info(
                "Pushed cortex-vision session %s to Pi (mode=%s, %d scenes)",
                session.get("id"),
                mode,
                scene_count,
            )
            return True
        except Exception as exc:
            logger.warning("Pi send_note failed: %s", exc)
            return False
