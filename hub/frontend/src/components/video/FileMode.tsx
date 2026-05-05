import { useState } from 'react'
import { useVideoJob } from '../../hooks/useVideoJob'
import {
  STATUS_DISPLAY,
  frameUrl,
  isTerminal,
  type SceneEntry,
  type VideoSession,
} from '../../lib/videoApi'

/** Phase 1 batch mode — paste a video URL, get back scenes + narrative.
 *
 * Wired to cortex-vision's POST /api/video/jobs (proxied through the Hub).
 * Polls /api/video/sessions/{id} every 2s until the session reaches a
 * terminal state. Frames are served via the proxy as raw JPEGs.
 */
export function FileMode() {
  const [url, setUrl] = useState('')
  const { session, submitting, error, submit, reset } = useVideoJob()

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const trimmed = url.trim()
    if (!trimmed) return
    await submit({ source: trimmed, mode: 'file' })
  }

  const isRunning = session !== null && !isTerminal(session.status)

  return (
    <div className="max-w-4xl mx-auto p-6 space-y-6">
      {/* URL input */}
      <form
        onSubmit={handleSubmit}
        className="bg-surface rounded-xl p-5 border border-border"
      >
        <label className="block">
          <span className="text-sm font-semibold text-text-primary">
            Process video by URL
          </span>
          <span className="block text-xs text-text-muted mt-0.5">
            YouTube, TikTok, or any source yt-dlp supports. Local file paths
            also accepted (absolute paths only).
          </span>
        </label>
        <div className="flex gap-2 mt-3">
          <input
            type="text"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://www.youtube.com/watch?v=..."
            disabled={isRunning || submitting}
            className="flex-1 px-3 py-2 text-sm rounded-lg bg-surface-secondary border border-border focus:border-accent focus:outline-none text-text-primary placeholder:text-text-muted disabled:opacity-50"
          />
          <button
            type="submit"
            disabled={!url.trim() || isRunning || submitting}
            className="px-4 py-2 text-sm rounded-lg bg-accent text-white hover:bg-accent-hover disabled:opacity-50 cursor-pointer"
          >
            {submitting ? 'Submitting…' : 'Process'}
          </button>
          {session !== null && (
            <button
              type="button"
              onClick={reset}
              className="px-3 py-2 text-sm rounded-lg border border-border text-text-secondary hover:bg-surface-tertiary cursor-pointer"
            >
              Clear
            </button>
          )}
        </div>
      </form>

      {error && !session && (
        <div className="bg-error/10 border border-error/30 rounded-lg p-3 text-sm text-error whitespace-pre-wrap">
          {error}
        </div>
      )}

      {session && <SessionView session={session} />}
    </div>
  )
}

function SessionView({ session }: { session: VideoSession }) {
  const display = STATUS_DISPLAY[session.status]
  const totalScenes = (session.progress?.total_scenes as number) ?? 0
  const currentScene = (session.progress?.current_scene as number) ?? 0
  const progressPct =
    totalScenes > 0 ? Math.min(100, (currentScene / totalScenes) * 100) : null

  return (
    <div className="space-y-4">
      {/* Status header */}
      <div className="bg-surface rounded-xl p-4 border border-border">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span
                className={`text-xs font-semibold px-2 py-0.5 rounded ${display.color}`}
              >
                {display.label}
              </span>
              <span className="text-xs text-text-muted font-mono">
                {session.id.slice(0, 8)}
              </span>
            </div>
            <p className="text-xs text-text-muted mt-1 truncate">
              {(session.source as { url?: string })?.url ??
                JSON.stringify(session.source)}
            </p>
          </div>
          <SceneCounter
            scenes={session.scenes.length}
            total={totalScenes || null}
          />
        </div>

        {progressPct !== null && session.status !== 'complete' && (
          <div className="mt-3 h-1.5 bg-surface-tertiary rounded-full overflow-hidden">
            <div
              className="h-full bg-accent transition-[width] duration-500"
              style={{ width: `${progressPct}%` }}
            />
          </div>
        )}

        {session.error && (
          <p className="mt-3 text-xs text-error whitespace-pre-wrap">
            {session.error}
          </p>
        )}
      </div>

      {/* Scenes */}
      {session.scenes.length > 0 && (
        <div className="bg-surface rounded-xl p-4 border border-border space-y-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-text-muted">
            Scenes
          </h3>
          <div className="space-y-3">
            {session.scenes.map((scene) => (
              <SceneCard
                key={scene.index}
                scene={scene}
                sessionId={session.id}
              />
            ))}
          </div>
        </div>
      )}

      {/* Narrative */}
      {session.narrative && (
        <div className="bg-surface rounded-xl p-5 border border-border">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-text-muted mb-2">
            Narrative
          </h3>
          <p className="text-sm text-text-secondary leading-relaxed whitespace-pre-wrap">
            {session.narrative}
          </p>
        </div>
      )}
    </div>
  )
}

function SceneCounter({
  scenes,
  total,
}: {
  scenes: number
  total: number | null
}) {
  return (
    <div className="text-right shrink-0">
      <div className="text-sm font-semibold text-text-primary">
        {scenes}
        {total ? ` / ${total}` : ''}
      </div>
      <div className="text-[10px] uppercase tracking-wide text-text-muted">
        scenes
      </div>
    </div>
  )
}

function SceneCard({
  scene,
  sessionId,
}: {
  scene: SceneEntry
  sessionId: string
}) {
  return (
    <div className="bg-surface-secondary rounded-lg p-3 border border-border">
      <div className="flex gap-3">
        {/* Thumbnail (first keyframe) */}
        {scene.keyframe_paths.length > 0 && (
          <img
            src={frameUrl(sessionId, scene.index, 0)}
            alt={`Scene ${scene.index}`}
            className="w-32 h-20 object-cover rounded shrink-0 bg-surface-tertiary"
            loading="lazy"
          />
        )}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-xs font-mono text-text-muted">
              #{scene.index}
            </span>
            <span className="text-xs text-text-muted">
              {fmtTime(scene.start_s)}–{fmtTime(scene.end_s)}
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
              awaiting description…
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

function fmtTime(s: number): string {
  const m = Math.floor(s / 60)
  const r = Math.floor(s % 60)
  return `${m}:${r.toString().padStart(2, '0')}`
}
