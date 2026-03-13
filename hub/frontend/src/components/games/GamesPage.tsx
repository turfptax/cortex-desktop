import { useState } from 'react'
import { PongCanvas } from './PongCanvas'
import { PongTraining } from './PongTraining'

type Tab = 'play' | 'train'

export function GamesPage() {
  const [activeTab, setActiveTab] = useState<Tab>('play')

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-6 py-3 border-b border-border bg-surface-secondary flex items-center justify-between">
        <h2 className="text-sm font-semibold text-text-primary">Games</h2>
        <div className="flex gap-1 bg-surface-tertiary rounded-lg p-0.5">
          {(['play', 'train'] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors capitalize ${
                activeTab === tab
                  ? 'bg-accent text-white'
                  : 'text-text-secondary hover:text-text-primary'
              }`}
            >
              {tab === 'play' ? 'Play' : 'Train AI'}
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto">
        {activeTab === 'play' && (
          <div className="flex items-center justify-center h-full p-4">
            <PongCanvas />
          </div>
        )}
        {activeTab === 'train' && <PongTraining />}
      </div>
    </div>
  )
}
