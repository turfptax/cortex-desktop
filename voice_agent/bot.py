#!/usr/bin/env python3
"""Cortex voice agent: a two-tier, real-time conversational front to the overseer.

Pipeline (pipecat, SmallWebRTC): browser mic -> Whisper STT -> tier-1 model
-> Kokoro TTS -> browser speaker, with Silero VAD turn-taking and barge-in.

Two tiers:
  - Tier 1 is a fast, cheap CLOUD model (Gemini Flash via OpenRouter). It owns
    the conversation and decides when a turn needs real memory.
  - For any memory/factual question it calls the ask_overseer tool, which hits
    the full overseer agent on the Pi (Opus, full corpus) and relays the answer.

Run standalone (serves a prebuilt web UI + the activity monitor):
    python -m voice_agent.bot -t webrtc
then open http://localhost:7860/ (voice) and http://localhost:7861/ (monitor).

On Windows set PYTHONUTF8=1 so the runner's startup banner can print.
"""
from __future__ import annotations

from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.kokoro.tts import KokoroTTSService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.whisper.stt import WhisperSTTService, WhisperSTTSettings
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.workers.runner import WorkerRunner

from . import config as cfg
from . import subagent, tools
from .activity import record, start_monitor

# ── Pipeline ─────────────────────────────────────────────────────────

def _build_stt() -> WhisperSTTService:
    return WhisperSTTService(
        model=cfg.STT_MODEL, device=cfg.STT_DEVICE, compute_type=cfg.STT_COMPUTE,
        settings=WhisperSTTSettings(extra={"initial_prompt": cfg.STT_VOCAB}))


def _build_tts() -> KokoroTTSService:
    # Use staged voice files when present; otherwise let Kokoro auto-download.
    if cfg.KOKORO_ONNX.is_file() and cfg.KOKORO_VOICES.is_file():
        return KokoroTTSService(
            voice_id=cfg.KOKORO_VOICE,
            model_path=str(cfg.KOKORO_ONNX),
            voices_path=str(cfg.KOKORO_VOICES))
    logger.warning(f"Kokoro voice files not in {cfg.MODELS_DIR}; auto-downloading")
    return KokoroTTSService(voice_id=cfg.KOKORO_VOICE)


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments) -> None:
    stt = _build_stt()
    tts = _build_tts()
    llm = OpenAILLMService(
        model=cfg.TIER1_MODEL, api_key=cfg.OPENROUTER_KEY, base_url=cfg.TIER1_BASE)
    tools.register_all(llm)

    context = LLMContext(
        messages=[{"role": "system", "content": cfg.build_system_prompt()}],
        tools=tools.ALL_SCHEMAS)
    user_agg, assistant_agg = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()))

    pipeline = Pipeline([
        transport.input(),
        stt,
        user_agg,
        llm,
        tts,
        transport.output(),
        assistant_agg,
    ])
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True))

    # Surface a finished background sub-agent into the conversation: add a
    # developer note the model relays on its next turn (no forced interruption).
    subagent.set_announcer(
        lambda text: context.add_message({"role": "developer", "content": text}))

    @user_agg.event_handler("on_user_turn_stopped")
    async def _on_user_turn(aggregator, strategy, message):
        record("user", text=(getattr(message, "content", "") or ""))

    @assistant_agg.event_handler("on_assistant_turn_stopped")
    async def _on_assistant_turn(aggregator, message):
        record("assistant", text=(getattr(message, "content", "") or ""))

    @worker.rtvi.event_handler("on_client_ready")
    async def on_client_ready(rtvi):
        context.add_message({
            "role": "developer",
            "content": "Greet me warmly in one short spoken sentence."})
        await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("client connected")

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("client disconnected")
        await worker.cancel()

    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    await runner.run()


async def bot(runner_args: RunnerArguments) -> None:
    transport_params = {
        "webrtc": lambda: TransportParams(
            audio_in_enabled=True, audio_out_enabled=True),
    }
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args)


if __name__ == "__main__":
    start_monitor(cfg.MONITOR_PORT)
    from pipecat.runner.run import main

    main()
