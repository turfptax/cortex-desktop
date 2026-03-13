import { useEffect, useState } from 'react'
import { usePi } from '../../hooks/usePi'
import { StatusCard } from './StatusCard'
import { PetChat } from './PetChat'
import { NotesPanel } from './NotesPanel'
import { PetCareTab } from './PetCareTab'
import { type StatusInfo } from '../../App'

interface Props {
  status: StatusInfo
}

export function PiPage({ status }: Props) {
  const pi = usePi()
  const [activeTab, setActiveTab] = useState<
    'status' | 'pet' | 'care' | 'notes'
  >('status')

  useEffect(() => {
    pi.fetchStatus()
    pi.fetchNotes()
  }, [])

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-6 py-3 border-b border-border bg-surface-secondary">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <h2 className="text-base font-semibold text-text-primary">
              Pi Zero
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
          <div className="flex gap-1 bg-surface-tertiary rounded-lg p-0.5">
            {(['status', 'pet', 'care', 'notes'] as const).map((tab) => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors capitalize cursor-pointer ${
                  activeTab === tab
                    ? 'bg-accent text-white'
                    : 'text-text-secondary hover:text-text-primary'
                }`}
              >
                {tab === 'pet'
                  ? 'Pet Chat'
                  : tab === 'care'
                    ? 'Pet Care'
                    : tab}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-hidden">
        {activeTab === 'status' && (
          <div className="p-6 overflow-y-auto h-full">
            <StatusCard
              piStatus={pi.piStatus}
              onRefresh={pi.fetchStatus}
            />
          </div>
        )}
        {activeTab === 'pet' && (
          <PetChat
            messages={pi.petMessages}
            isLoading={pi.isPetLoading}
            onSend={pi.sendPetMessage}
            isOnline={status.piOnline}
          />
        )}
        {activeTab === 'care' && (
          <PetCareTab isOnline={status.piOnline} />
        )}
        {activeTab === 'notes' && (
          <NotesPanel
            notes={pi.notes}
            onRefresh={pi.fetchNotes}
            onSend={pi.sendNote}
            isOnline={status.piOnline}
          />
        )}
      </div>
    </div>
  )
}
