import { useCallback, useEffect, useState } from 'react'
import { apiFetch } from '../../lib/api'

interface HeartbeatEntry {
  id: number
  prompt_type: string
  prompt: string
  response: string
  sentiment_score: number
  inference_time_ms: number
  tokens_generated: number
  battery_pct: number
  is_charging: number
  hunger: number
  cleanliness: number
  energy: number
  happiness: number
  shell_commands: string
  shell_results: string
  created_at: string
}

interface HeartbeatStatus {
  enabled: boolean
  running: boolean
  interval_s: number
  is_sleeping: boolean
  is_dreaming: boolean
  total_heartbeats: number
}

interface Props {
  isOnline: boolean
}

const PROMPT_TYPE_LABELS: Record<string, { label: string; icon: string; color: string }> = {
  vitals_check: { label: 'Vitals Check', icon: '💓', color: '#ff6b6b' },
  body_awareness: { label: 'Body Awareness', icon: '🤖', color: '#51cf66' },
  time_awareness: { label: 'Time Awareness', icon: '🕐', color: '#339af0' },
  memory_reflection: { label: 'Memory', icon: '🧠', color: '#cc5de8' },
  dream_reflection: { label: 'Dream', icon: '💭', color: '#ffd43b' },
}

function sentimentBar(score: number) {
  const pct = Math.round(score * 100)
  const color = score > 0.6 ? '#51cf66' : score > 0.3 ? '#ffd43b' : '#ff6b6b'
  return (
    <div className="flex items-center gap-2 text-xs">
      <div className="w-16 h-1.5 bg-surface-tertiary rounded-full overflow-hidden">
        <div className="h-full rounded-full" style={{ width: `${pct}%`, backgroundColor: color }} />
      </div>
      <span className="text-text-muted">{pct}%</span>
    </div>
  )
}

function timeAgo(dateStr: string): string {
  const now = new Date()
  const then = new Date(dateStr + 'Z')
  const diffMs = now.getTime() - then.getTime()
  const mins = Math.floor(diffMs / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  return `${days}d ago`
}

export function HeartbeatTab({ isOnline }: Props) {
  const [entries, setEntries] = useState<HeartbeatEntry[]>([])
  const [status, setStatus] = useState<HeartbeatStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState<string>('all')

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const [logRes, statusRes] = await Promise.all([
        apiFetch('/pi/pet/heartbeat-log?limit=100'),
        apiFetch('/pi/pet/heartbeat-status'),
      ])
      if (Array.isArray(logRes)) setEntries(logRes)
      if (statusRes && typeof statusRes === 'object') setStatus(statusRes as HeartbeatStatus)
    } catch (e) {
      console.error('Failed to fetch heartbeat data:', e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (isOnline) fetchData()
  }, [isOnline, fetchData])

  const filtered = filter === 'all'
    ? entries
    : entries.filter(e => e.prompt_type === filter)

  const typeCounts = entries.reduce<Record<string, number>>((acc, e) => {
    acc[e.prompt_type] = (acc[e.prompt_type] || 0) + 1
    return acc
  }, {})

  if (!isOnline) {
    return (
      <div className="flex items-center justify-center h-full text-text-muted">
        Pi is offline
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full">
      {/* Status bar */}
      {status && (
        <div className="px-4 py-2 border-b border-border bg-surface-secondary flex items-center gap-4 text-xs">
          <span className={`flex items-center gap-1 ${status.running ? 'text-success' : 'text-text-muted'}`}>
            <span className={`w-2 h-2 rounded-full ${status.running ? 'bg-success animate-pulse' : 'bg-text-muted'}`} />
            {status.running ? 'Active' : 'Stopped'}
          </span>
          <span className="text-text-muted">
            Interval: {Math.round(status.interval_s / 60)}min
          </span>
          <span className="text-text-muted">
            Total: {status.total_heartbeats}
          </span>
          {status.is_sleeping && (
            <span className="text-accent">Sleeping</span>
          )}
          {status.is_dreaming && (
            <span className="text-warning">Dreaming</span>
          )}
          <button
            onClick={fetchData}
            className="ml-auto px-2 py-0.5 rounded bg-surface-tertiary hover:bg-accent/20 text-text-secondary cursor-pointer"
          >
            Refresh
          </button>
        </div>
      )}

      {/* Filter bar */}
      <div className="px-4 py-2 border-b border-border flex gap-1 flex-wrap">
        <button
          onClick={() => setFilter('all')}
          className={`px-2 py-1 rounded text-xs cursor-pointer transition-colors ${
            filter === 'all' ? 'bg-accent text-white' : 'bg-surface-tertiary text-text-secondary hover:text-text-primary'
          }`}
        >
          All ({entries.length})
        </button>
        {Object.entries(PROMPT_TYPE_LABELS).map(([type, { label, icon }]) => (
          <button
            key={type}
            onClick={() => setFilter(type)}
            className={`px-2 py-1 rounded text-xs cursor-pointer transition-colors ${
              filter === type ? 'bg-accent text-white' : 'bg-surface-tertiary text-text-secondary hover:text-text-primary'
            }`}
          >
            {icon} {label} ({typeCounts[type] || 0})
          </button>
        ))}
      </div>

      {/* Entries */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {loading ? (
          <div className="text-center text-text-muted py-8">Loading heartbeats...</div>
        ) : filtered.length === 0 ? (
          <div className="text-center text-text-muted py-8">No heartbeat entries found</div>
        ) : (
          filtered.map((entry) => {
            const typeInfo = PROMPT_TYPE_LABELS[entry.prompt_type] || {
              label: entry.prompt_type, icon: '❓', color: '#868e96',
            }
            return (
              <div key={entry.id} className="bg-surface-secondary rounded-lg border border-border p-3">
                {/* Header */}
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <span
                      className="px-2 py-0.5 rounded-full text-xs font-medium"
                      style={{ backgroundColor: typeInfo.color + '20', color: typeInfo.color }}
                    >
                      {typeInfo.icon} {typeInfo.label}
                    </span>
                    <span className="text-xs text-text-muted">{timeAgo(entry.created_at)}</span>
                  </div>
                  <div className="flex items-center gap-3 text-xs text-text-muted">
                    <span>🔋 {entry.battery_pct}%{entry.is_charging ? '⚡' : ''}</span>
                    <span>{entry.tokens_generated} tok</span>
                    <span>{(entry.inference_time_ms / 1000).toFixed(1)}s</span>
                  </div>
                </div>

                {/* Response bubble */}
                <div className="bg-surface-tertiary rounded-lg px-3 py-2 mb-2">
                  <p className="text-sm text-text-primary leading-relaxed">
                    {entry.response || <span className="text-text-muted italic">No response</span>}
                  </p>
                </div>

                {/* Footer: sentiment + vitals snapshot */}
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1 text-xs text-text-muted">
                    <span>Sentiment:</span>
                    {sentimentBar(entry.sentiment_score)}
                  </div>
                  <div className="flex items-center gap-2 text-xs text-text-muted">
                    <span>🍖{Math.round(entry.hunger * 100)}%</span>
                    <span>🧹{Math.round(entry.cleanliness * 100)}%</span>
                    <span style={{ color: '#ff3c00' }}>⚡{Math.round(entry.energy * 100)}%</span>
                    <span>😊{Math.round(entry.happiness * 100)}%</span>
                  </div>
                </div>

                {/* Shell commands if any */}
                {entry.shell_commands && (
                  <div className="mt-2 bg-black/30 rounded p-2 text-xs font-mono text-green-400">
                    <div>$ {entry.shell_commands}</div>
                    {entry.shell_results && <div className="text-gray-400 mt-1">{entry.shell_results}</div>}
                  </div>
                )}

                {/* Expandable prompt */}
                <details className="mt-2">
                  <summary className="text-xs text-text-muted cursor-pointer hover:text-text-secondary">
                    Show prompt
                  </summary>
                  <p className="text-xs text-text-muted mt-1 leading-relaxed">{entry.prompt}</p>
                </details>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
