import { useState } from 'react'
import { FileMode } from './FileMode'
import { JournalMode } from './JournalMode'
import { SessionList } from './SessionList'

/** Video page — replaces the placeholder once cortex-vision is registered.
 *
 * Phases enabled so far: File (Phase 1), Journal (Phase 3), History
 * (cross-mode session list). Live remains gated until Phase 4
 * (OBS virtual cam + WebSocket pass-through).
 */
type VideoTab = 'file' | 'journal' | 'live' | 'history'

interface TabSpec {
  id: VideoTab
  label: string
  enabled: boolean
  comingIn?: string
}

const TABS: TabSpec[] = [
  { id: 'file', label: 'Process video', enabled: true },
  { id: 'journal', label: 'Video journal', enabled: true },
  { id: 'history', label: 'History', enabled: true },
  { id: 'live', label: 'Live (OBS)', enabled: false, comingIn: 'Phase 4' },
]

export function VideoPage() {
  const [tab, setTab] = useState<VideoTab>('file')

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <header className="px-6 py-4 border-b border-border shrink-0">
        <div className="flex items-baseline justify-between gap-4">
          <div>
            <h1 className="text-xl font-bold text-text-primary">Video</h1>
            <p className="text-xs text-text-muted mt-0.5">
              Cortex Vision sidecar — process videos, journal, and watch your
              screen live
            </p>
          </div>
        </div>
        <nav className="flex items-center gap-1 mt-3">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => t.enabled && setTab(t.id)}
              disabled={!t.enabled}
              title={
                t.enabled ? undefined : `Coming in ${t.comingIn} of cortex-vision`
              }
              className={`px-3 py-1.5 text-xs rounded-lg cursor-pointer transition-colors ${
                tab === t.id
                  ? 'bg-accent/15 text-accent-hover'
                  : t.enabled
                    ? 'text-text-secondary hover:bg-surface-tertiary hover:text-text-primary'
                    : 'text-text-muted/50 cursor-not-allowed'
              }`}
            >
              {t.label}
              {!t.enabled && t.comingIn && (
                <span className="ml-1.5 text-[10px] opacity-60">{t.comingIn}</span>
              )}
            </button>
          ))}
        </nav>
      </header>

      <div className="flex-1 overflow-y-auto">
        {tab === 'file' && <FileMode />}
        {tab === 'journal' && <JournalMode />}
        {tab === 'history' && <SessionList />}
        {/* live tab disabled until Phase 4 */}
      </div>
    </div>
  )
}
