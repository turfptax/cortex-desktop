import { useEffect, useState } from 'react'
import { usePi } from '../../hooks/usePi'
import { StatusCard } from './StatusCard'
import { NotesPanel } from './NotesPanel'
import { FirmwareUpdate } from './FirmwareUpdate'
import { TrainingPage } from '../training/TrainingPage'
import { GamesPage } from '../games/GamesPage'
import { type StatusInfo } from '../../App'

interface Props {
  status: StatusInfo
}

// Pi inner tabs. `system` rolls up status + firmware (both low-frequency,
// system-level). Was 8 tabs after sidebar reorg; this collapses to 7 and
// the strip below uses overflow-x-auto so future additions don't break
// layout on narrow widths.
const PI_TABS = ['system', 'notes', 'training', 'games'] as const
type PiTab = typeof PI_TABS[number]

const TAB_LABELS: Record<PiTab, string> = {
  system: 'System',
  notes: 'Notes',
  training: 'Training',
  games: 'Games',
}

export function PiPage({ status }: Props) {
  const pi = usePi()
  const [activeTab, setActiveTab] = useState<PiTab>('system')

  useEffect(() => {
    pi.fetchStatus()
    pi.fetchNotes()
  }, [])

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-6 py-3 border-b border-border bg-surface-secondary">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-3 shrink-0">
            <h2 className="text-base font-semibold text-text-primary">
              Cortex Pi
            </h2>
            <span
              className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                status.piOnline
                  ? 'bg-success/20 text-success'
                  : 'bg-text-muted/20 text-text-muted'
              }`}
            >
              {status.piOnline ? 'Connected' : 'Offline'}
            </span>
          </div>
          <div className="flex gap-1 bg-surface-tertiary rounded-lg p-0.5 overflow-x-auto whitespace-nowrap">
            {PI_TABS.map((tab) => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors cursor-pointer ${
                  activeTab === tab
                    ? 'bg-accent text-white'
                    : 'text-text-secondary hover:text-text-primary'
                }`}
              >
                {TAB_LABELS[tab]}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-hidden">
        {activeTab === 'system' && (
          <div className="p-6 overflow-y-auto h-full space-y-6">
            <StatusCard
              piStatus={pi.piStatus}
              onRefresh={pi.fetchStatus}
            />
            <div className="border-t border-border pt-6">
              <FirmwareUpdate isOnline={status.piOnline} />
            </div>
          </div>
        )}
        {activeTab === 'notes' && (
          <NotesPanel
            notes={pi.notes}
            onRefresh={pi.fetchNotes}
            onSend={pi.sendNote}
            isOnline={status.piOnline}
          />
        )}
        {activeTab === 'training' && <TrainingPage />}
        {activeTab === 'games' && <GamesPage />}
      </div>
    </div>
  )
}
