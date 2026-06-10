import { useState } from 'react'
import { type StatusInfo } from '../../App'
import { PiPage } from '../pi/PiPage'
import { DataPage } from '../data/DataPage'
import { VideoPage } from '../video/VideoPage'
import { ActivityPanel } from '../overseer/ActivityPanel'

/** UI redesign Phase 1+2 (2026-06): the ops section. Absorbs the old
 * top-level Pi, Data, and Video tabs plus the overseer's Activity
 * feed as sub-tabs; the pages themselves are mounted unchanged. */

type SystemTab = 'pi' | 'data' | 'activity' | 'video'

export function SystemPage({
  status,
  visionRunning,
}: {
  status: StatusInfo
  visionRunning: boolean
}) {
  const [tab, setTab] = useState<SystemTab>('pi')
  const tabs: { id: SystemTab; label: string }[] = [
    { id: 'pi', label: 'Pi' },
    { id: 'data', label: 'Data' },
    { id: 'activity', label: 'Activity' },
    ...(visionRunning
      ? [{ id: 'video' as SystemTab, label: 'Video' }]
      : []),
  ]
  const active = tab === 'video' && !visionRunning ? 'pi' : tab

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="flex gap-1 px-4 pt-3 border-b border-border shrink-0">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`px-3 py-2 text-sm font-medium rounded-t-lg transition-colors cursor-pointer ${
              active === t.id
                ? 'bg-surface-secondary text-accent-hover'
                : 'text-text-secondary hover:text-text-primary'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="flex-1 flex flex-col overflow-hidden">
        {active === 'pi' && <PiPage status={status} />}
        {active === 'data' && <DataPage status={status} />}
        {active === 'activity' && (
          <div className="flex-1 overflow-y-auto p-6">
            <ActivityPanel />
          </div>
        )}
        {active === 'video' && visionRunning && <VideoPage />}
      </div>
    </div>
  )
}
