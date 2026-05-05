/** Placeholder for the Video page.
 *
 * Phase 0 of the v0.18 cycle reserves the routing slot but does NOT
 * ship any video components — those are delivered by cortex-vision's
 * Phase 1 (FileMode), Phase 3 (JournalMode), and Phase 4 (LiveMode).
 *
 * If you're reading this and the Phase 1 components have landed,
 * replace this component with the real VideoPage.
 */
export function VideoPlaceholder() {
  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <header className="px-6 py-4 border-b border-border shrink-0">
        <div>
          <h1 className="text-xl font-bold text-text-primary">Video</h1>
          <p className="text-xs text-text-muted mt-0.5">
            Cortex Vision sidecar — connected
          </p>
        </div>
      </header>
      <div className="flex-1 overflow-y-auto p-8">
        <div className="max-w-2xl mx-auto bg-surface rounded-xl p-6 border border-border">
          <h2 className="text-base font-semibold text-text-primary mb-2">
            Plugin connected, UI pending
          </h2>
          <p className="text-sm text-text-secondary leading-relaxed">
            Cortex Vision is registered and reachable. The UI for batch
            video processing, video journals, and live screen capture
            ships in upcoming Phases (1, 3, 4) of the cortex-vision
            roadmap and lands here automatically.
          </p>
          <p className="text-sm text-text-secondary leading-relaxed mt-3">
            For now, the sidecar exposes its endpoints under{' '}
            <code className="text-xs bg-surface-tertiary px-1.5 py-0.5 rounded">
              /api/video/*
            </code>{' '}
            — useful for testing the proxy with{' '}
            <code className="text-xs bg-surface-tertiary px-1.5 py-0.5 rounded">
              curl http://localhost:8003/api/video/health
            </code>
            .
          </p>
        </div>
      </div>
    </div>
  )
}
