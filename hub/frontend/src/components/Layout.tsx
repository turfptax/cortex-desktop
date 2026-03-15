import { type ReactNode, useState } from 'react'
import { type Page, type StatusInfo } from '../App'
import { PetWidget, type PetStatus } from './PetWidget'
import { DisplayEmulator } from './DisplayEmulator'

interface LayoutProps {
  page: Page
  setPage: (page: Page) => void
  status: StatusInfo
  petStatus: PetStatus | null
  children: ReactNode
}

const navItems: { id: Page; label: string; icon: string }[] = [
  { id: 'chat', label: 'Chat', icon: '💬' },
  { id: 'training', label: 'Training', icon: '🧠' },
  { id: 'pi', label: 'Pi', icon: '🥧' },
  { id: 'games', label: 'Games', icon: '🎮' },
  { id: 'data', label: 'Data', icon: '📊' },
  { id: 'settings', label: 'Settings', icon: '⚙️' },
]

export function Layout({ page, setPage, status, petStatus, children }: LayoutProps) {
  const [useVoxels, setUseVoxels] = useState(false)

  return (
    <div className="flex h-screen">
      {/* Sidebar */}
      <aside className="w-56 bg-surface-secondary border-r border-border flex flex-col shrink-0">
        {/* Logo */}
        <div className="p-4 border-b border-border">
          <h1 className="text-lg font-bold text-text-primary">Cortex Hub</h1>
          <p className="text-xs text-text-muted mt-0.5">Control Center</p>
        </div>

        {/* Navigation */}
        <nav className="p-2 space-y-1">
          {navItems.map((item) => (
            <button
              key={item.id}
              onClick={() => setPage(item.id)}
              className={`w-full text-left px-3 py-2.5 rounded-lg flex items-center gap-3 transition-colors cursor-pointer ${
                page === item.id
                  ? 'bg-accent/15 text-accent-hover'
                  : 'text-text-secondary hover:bg-surface-tertiary hover:text-text-primary'
              }`}
            >
              <span className="text-lg">{item.icon}</span>
              <span className="font-medium text-sm">{item.label}</span>
            </button>
          ))}
        </nav>

        {/* Pi display emulator */}
        <DisplayEmulator
          petStatus={petStatus}
          piOnline={status.piOnline}
          useVoxels={useVoxels}
        />
        <button
          onClick={() => setUseVoxels(v => !v)}
          className="mx-2 mb-1 px-2 py-0.5 text-[10px] text-text-muted hover:text-accent rounded border border-border hover:border-accent/50 transition-colors cursor-pointer"
        >
          {useVoxels ? '🧊 Voxel View' : '🐱 Sprite View'}
        </button>

        {/* Scrollable middle section */}
        <div className="flex-1 overflow-y-auto min-h-0">
          {/* Pet widget */}
          {petStatus && <PetWidget pet={petStatus} />}
        </div>

        {/* Status indicators */}
        <div className="p-3 border-t border-border space-y-2">
          <StatusDot label="LM Studio" online={status.lmstudioOnline} />
          <StatusDot label="Pi Zero" online={status.piOnline} />
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 flex flex-col overflow-hidden">
        {children}
      </main>
    </div>
  )
}

function StatusDot({ label, online }: { label: string; online: boolean }) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <div
        className={`w-2 h-2 rounded-full ${
          online ? 'bg-success' : 'bg-text-muted'
        }`}
      />
      <span className="text-text-muted">{label}</span>
      <span className={online ? 'text-success' : 'text-text-muted'}>
        {online ? 'Online' : 'Offline'}
      </span>
    </div>
  )
}
