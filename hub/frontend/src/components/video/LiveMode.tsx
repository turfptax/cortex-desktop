import { useEffect, useRef, useState } from 'react'
import {
  liveStart,
  liveStop,
  liveStatus,
  liveWsUrl,
  listCameras,
  type CameraInfo,
  type LiveEvent,
} from '../../lib/videoApi'

/** Phase 4 live capture mode.
 *
 * Subscribes to WS /api/video/live/ws (proxied through cortex-desktop)
 * and renders scenes as the cortex-vision sidecar emits them. Audio is
 * intentionally absent — the locked design says overseer's existing
 * audio-journal flow handles speech and Phase 6 wires the bridge.
 *
 * Reconnect is intentionally NOT implemented: a live session is a
 * single thing the user explicitly started; if the connection drops
 * mid-session, surfacing that to the user is more useful than silently
 * reconnecting and possibly missing events.
 */

interface SceneState {
  index: number
  thumbnail_url: string
  trigger_method: string
  similarity: number
  description?: string
  describer_model?: string
}

interface LiveStats {
  fps: number
  frames: number
  scene_count: number
  elapsed_s: number
}

type Phase = 'idle' | 'starting' | 'running' | 'stopping' | 'stopped' | 'error'

export function LiveMode() {
  const [phase, setPhase] = useState<Phase>('idle')
  const [error, setError] = useState<string | null>(null)
  const [cameras, setCameras] = useState<CameraInfo[]>([])
  const [cameraIndex, setCameraIndex] = useState(0)
  const [resolution, setResolution] = useState<[number, number]>([384, 216])
  const [pushToOverseer, setPushToOverseer] = useState(true)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [scenes, setScenes] = useState<Map<number, SceneState>>(new Map())
  const [stats, setStats] = useState<LiveStats | null>(null)

  const wsRef = useRef<WebSocket | null>(null)

  // Probe for an already-running session on mount (refresh resilience)
  // and pre-fetch the camera list.
  useEffect(() => {
    let cancelled = false
    const init = async () => {
      try {
        const status = await liveStatus()
        if (!cancelled && (status as { is_running?: boolean }).is_running) {
          // Already running from a previous tab/session — adopt it.
          setPhase('running')
          openSocket()
        }
      } catch {
        /* sidecar might not have /status yet; harmless */
      }
      try {
        const list = await listCameras()
        if (!cancelled) setCameras(list)
      } catch (e) {
        if (!cancelled)
          setError(e instanceof Error ? e.message : String(e))
      }
    }
    init()
    return () => {
      cancelled = true
      closeSocket()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function openSocket(): void {
    if (wsRef.current) return
    const ws = new WebSocket(liveWsUrl())
    wsRef.current = ws

    ws.onmessage = (msg) => {
      let event: LiveEvent
      try {
        event = JSON.parse(msg.data)
      } catch {
        return
      }
      handleEvent(event)
    }

    ws.onclose = (ev) => {
      wsRef.current = null
      // Don't downgrade to 'error' if we asked to stop or it cleanly closed
      if (phase !== 'stopped' && phase !== 'stopping' && phase !== 'idle') {
        if (ev.code !== 1000 && ev.code !== 1005) {
          setError(`WebSocket closed (${ev.code}${ev.reason ? `: ${ev.reason}` : ''})`)
        }
      }
    }

    ws.onerror = () => {
      // Browser-side WS errors don't expose much; rely on onclose's code/reason
    }
  }

  function closeSocket(): void {
    if (wsRef.current) {
      try {
        wsRef.current.close(1000, 'client disconnecting')
      } catch {
        /* ignore */
      }
      wsRef.current = null
    }
  }

  function handleEvent(event: LiveEvent): void {
    switch (event.type) {
      case 'started':
        setPhase('running')
        setSessionId((event as { session_id: string }).session_id)
        break
      case 'scene': {
        const e = event as Extract<LiveEvent, { type: 'scene' }>
        setScenes((prev) => {
          const next = new Map(prev)
          const existing = next.get(e.scene_index)
          next.set(e.scene_index, {
            ...(existing ?? { index: e.scene_index }),
            index: e.scene_index,
            thumbnail_url: e.thumbnail_url,
            trigger_method: e.trigger_method,
            similarity: e.similarity,
          })
          return next
        })
        break
      }
      case 'described': {
        const e = event as Extract<LiveEvent, { type: 'described' }>
        setScenes((prev) => {
          const next = new Map(prev)
          const existing = next.get(e.scene_index)
          if (existing) {
            next.set(e.scene_index, {
              ...existing,
              description: e.description,
              describer_model: e.describer_model,
            })
          }
          return next
        })
        break
      }
      case 'stats': {
        const e = event as Extract<LiveEvent, { type: 'stats' }>
        setStats({
          fps: e.fps,
          frames: e.frames,
          scene_count: e.scene_count,
          elapsed_s: e.elapsed_s,
        })
        break
      }
      case 'stopped':
        setPhase('stopped')
        closeSocket()
        break
      case 'error':
        setError((event as { message: string }).message ?? 'Unknown error')
        setPhase('error')
        break
      default:
        // Forward-compatible: ignore unknown event types
        break
    }
  }

  const handleStart = async () => {
    setError(null)
    setPhase('starting')
    setScenes(new Map())
    setStats(null)
    try {
      await liveStart({
        camera_index: cameraIndex,
        resolution,
        // The push_to_overseer flag isn't accepted by /live/start in
        // Phase 4 — the bridge picks live sessions up via polling per
        // cortex-vision's bridge contract. Toggle is rendered for the
        // user but applied at the bridge layer once Phase 6 lands.
      })
      openSocket()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setPhase('error')
    }
  }

  const handleStop = async () => {
    setPhase('stopping')
    try {
      await liveStop()
      // The "stopped" event over WS finalizes the phase; if the WS is
      // already gone, force-finalize here.
      if (!wsRef.current) setPhase('stopped')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setPhase('error')
    }
  }

  const handleReset = () => {
    closeSocket()
    setPhase('idle')
    setError(null)
    setSessionId(null)
    setScenes(new Map())
    setStats(null)
  }

  const sceneList = Array.from(scenes.values()).sort(
    (a, b) => b.index - a.index, // newest first
  )

  return (
    <div className="max-w-5xl mx-auto p-6 space-y-6">
      {phase === 'idle' && (
        <SetupCard
          cameras={cameras}
          cameraIndex={cameraIndex}
          setCameraIndex={setCameraIndex}
          resolution={resolution}
          setResolution={setResolution}
          pushToOverseer={pushToOverseer}
          setPushToOverseer={setPushToOverseer}
          onStart={handleStart}
        />
      )}

      {phase === 'starting' && (
        <BannerCard
          dotClass="bg-amber-500"
          title="Starting live session…"
          subtitle="Opening camera, attaching detector + describer threads"
        />
      )}

      {(phase === 'running' || phase === 'stopping' || phase === 'stopped') && (
        <>
          <RunningHeader
            phase={phase}
            sessionId={sessionId}
            stats={stats}
            onStop={handleStop}
            onReset={handleReset}
          />
          <SceneGrid scenes={sceneList} />
        </>
      )}

      {phase === 'error' && (
        <div className="bg-error/10 border border-error/30 rounded-lg p-4">
          <p className="text-sm font-semibold text-error">Live session failed</p>
          {error && (
            <p className="text-xs text-error/80 mt-1 whitespace-pre-wrap">
              {error}
            </p>
          )}
          <button
            onClick={handleReset}
            className="mt-3 px-3 py-1.5 text-xs rounded border border-error/40 text-error hover:bg-error/10 cursor-pointer"
          >
            Reset
          </button>
        </div>
      )}

      {error && phase !== 'error' && (
        <div className="bg-amber-500/10 border border-amber-500/30 rounded-lg p-3 text-xs text-amber-400 whitespace-pre-wrap">
          {error}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function SetupCard({
  cameras,
  cameraIndex,
  setCameraIndex,
  resolution,
  setResolution,
  pushToOverseer,
  setPushToOverseer,
  onStart,
}: {
  cameras: CameraInfo[]
  cameraIndex: number
  setCameraIndex: (i: number) => void
  resolution: [number, number]
  setResolution: (r: [number, number]) => void
  pushToOverseer: boolean
  setPushToOverseer: (b: boolean) => void
  onStart: () => void
}) {
  const resOptions: Array<[string, [number, number]]> = [
    ['384 × 216 (default — fastest)', [384, 216]],
    ['640 × 360', [640, 360]],
    ['1280 × 720', [1280, 720]],
  ]
  return (
    <div className="bg-surface rounded-xl p-5 border border-border">
      <h2 className="text-sm font-semibold text-text-primary">
        Watch screen live (OBS Virtual Camera or capture device)
      </h2>
      <p className="text-xs text-text-muted mt-0.5">
        Continuous capture. The detector emits a scene event each time the
        screen meaningfully changes; the describer pushes a description as
        soon as the LLM finishes. Stats stream every second.
      </p>

      <div className="space-y-3 mt-4">
        <div>
          <label className="text-xs uppercase tracking-wide text-text-muted block mb-1">
            Camera
          </label>
          {cameras.length > 0 ? (
            <select
              value={cameraIndex}
              onChange={(e) => setCameraIndex(Number(e.target.value))}
              className="w-full px-3 py-2 text-sm rounded-lg bg-surface-secondary border border-border text-text-primary focus:border-accent focus:outline-none"
            >
              {cameras.map((c) => (
                <option key={c.index} value={c.index}>
                  #{c.index}
                  {c.name ? ` · ${c.name}` : ''}
                  {c.resolution ? ` (${c.resolution[0]}×${c.resolution[1]})` : ''}
                </option>
              ))}
            </select>
          ) : (
            <input
              type="number"
              min={0}
              value={cameraIndex}
              onChange={(e) => setCameraIndex(Number(e.target.value))}
              className="w-32 px-3 py-2 text-sm rounded-lg bg-surface-secondary border border-border text-text-primary focus:border-accent focus:outline-none"
            />
          )}
          {cameras.length === 0 && (
            <p className="text-[11px] text-text-muted mt-1">
              No cameras enumerated. OBS Virtual Camera is usually index 0
              when it's the only one running.
            </p>
          )}
        </div>

        <div>
          <label className="text-xs uppercase tracking-wide text-text-muted block mb-1">
            Capture resolution
          </label>
          <select
            value={`${resolution[0]}x${resolution[1]}`}
            onChange={(e) => {
              const opt = resOptions.find(
                ([, r]) => `${r[0]}x${r[1]}` === e.target.value,
              )
              if (opt) setResolution(opt[1])
            }}
            className="w-full px-3 py-2 text-sm rounded-lg bg-surface-secondary border border-border text-text-primary focus:border-accent focus:outline-none"
          >
            {resOptions.map(([label, r]) => (
              <option key={label} value={`${r[0]}x${r[1]}`}>
                {label}
              </option>
            ))}
          </select>
        </div>

        <label className="flex items-center gap-2 text-sm text-text-secondary cursor-pointer">
          <input
            type="checkbox"
            checked={pushToOverseer}
            onChange={(e) => setPushToOverseer(e.target.checked)}
            className="w-4 h-4 rounded border-border accent-accent cursor-pointer"
          />
          Push 5-min rollups to overseer (handled by the bridge)
        </label>
      </div>

      <button
        onClick={onStart}
        className="mt-4 px-4 py-2 text-sm rounded-lg bg-accent text-white hover:bg-accent-hover cursor-pointer"
      >
        Start live session
      </button>
    </div>
  )
}

function BannerCard({
  dotClass,
  title,
  subtitle,
}: {
  dotClass: string
  title: string
  subtitle?: string
}) {
  return (
    <div className="bg-surface rounded-xl p-4 border border-border flex items-center gap-3">
      <span className={`w-2.5 h-2.5 rounded-full ${dotClass} animate-pulse`} />
      <div>
        <p className="text-sm font-semibold text-text-primary">{title}</p>
        {subtitle && (
          <p className="text-xs text-text-muted mt-0.5">{subtitle}</p>
        )}
      </div>
    </div>
  )
}

function RunningHeader({
  phase,
  sessionId,
  stats,
  onStop,
  onReset,
}: {
  phase: Phase
  sessionId: string | null
  stats: LiveStats | null
  onStop: () => void
  onReset: () => void
}) {
  const live = phase === 'running'
  return (
    <div className="bg-surface rounded-xl p-4 border border-border">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <span
            className={`w-2.5 h-2.5 rounded-full ${
              live ? 'bg-error animate-pulse' : 'bg-text-muted'
            }`}
          />
          <div>
            <p className="text-sm font-semibold text-text-primary">
              {live
                ? 'Live'
                : phase === 'stopping'
                  ? 'Stopping…'
                  : 'Stopped'}
            </p>
            {sessionId && (
              <p className="text-[11px] text-text-muted font-mono">
                {sessionId.slice(0, 8)}
              </p>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {live && (
            <button
              onClick={onStop}
              className="px-4 py-2 text-sm rounded-lg bg-error text-white hover:bg-error/90 cursor-pointer"
            >
              Stop
            </button>
          )}
          {phase === 'stopped' && (
            <button
              onClick={onReset}
              className="px-3 py-2 text-sm rounded-lg border border-border text-text-secondary hover:bg-surface-tertiary cursor-pointer"
            >
              Done
            </button>
          )}
        </div>
      </div>

      {stats && (
        <div className="mt-3 grid grid-cols-4 gap-3 text-center">
          <Stat label="FPS" value={stats.fps.toFixed(1)} />
          <Stat label="Frames" value={stats.frames.toLocaleString()} />
          <Stat label="Scenes" value={String(stats.scene_count)} />
          <Stat label="Elapsed" value={fmtElapsed(stats.elapsed_s)} />
        </div>
      )}
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-surface-secondary rounded-md p-2">
      <p className="text-sm font-mono text-text-primary">{value}</p>
      <p className="text-[10px] uppercase tracking-wide text-text-muted">
        {label}
      </p>
    </div>
  )
}

function SceneGrid({ scenes }: { scenes: SceneState[] }) {
  if (scenes.length === 0) {
    return (
      <div className="bg-surface rounded-xl p-8 border border-border text-center">
        <p className="text-sm text-text-muted">
          Watching for scene changes…
        </p>
      </div>
    )
  }
  return (
    <div className="bg-surface rounded-xl p-4 border border-border space-y-3">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-text-muted">
        Scenes ({scenes.length}, newest first)
      </h3>
      <div className="space-y-3">
        {scenes.map((s) => (
          <SceneCard key={s.index} scene={s} />
        ))}
      </div>
    </div>
  )
}

function SceneCard({ scene }: { scene: SceneState }) {
  return (
    <div className="bg-surface-secondary rounded-lg p-3 border border-border">
      <div className="flex gap-3">
        <img
          src={scene.thumbnail_url}
          alt={`Scene ${scene.index}`}
          className="w-32 h-20 object-cover rounded shrink-0 bg-surface-tertiary"
          loading="lazy"
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-xs font-mono text-text-muted">
              #{scene.index}
            </span>
            <span className="text-[10px] uppercase tracking-wide text-text-muted opacity-70">
              {scene.trigger_method} · sim {scene.similarity.toFixed(3)}
            </span>
            {scene.describer_model && (
              <span className="text-[10px] text-text-muted opacity-60 font-mono">
                {scene.describer_model}
              </span>
            )}
          </div>
          {scene.description ? (
            <p className="text-sm text-text-secondary mt-1 leading-snug">
              {scene.description}
            </p>
          ) : (
            <p className="text-xs text-text-muted italic mt-1">
              describing…
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

function fmtElapsed(s: number): string {
  const total = Math.floor(s)
  const m = Math.floor(total / 60)
  const sec = total % 60
  if (m >= 60) {
    const h = Math.floor(m / 60)
    return `${h}:${(m % 60).toString().padStart(2, '0')}:${sec.toString().padStart(2, '0')}`
  }
  return `${m}:${sec.toString().padStart(2, '0')}`
}
