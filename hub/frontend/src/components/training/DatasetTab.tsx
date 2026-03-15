import { useEffect, useState, useCallback } from 'react'
import { apiFetch } from '../../lib/api'

interface DatasetMessage {
  role: string
  content: string
}

interface DatasetExample {
  id: string
  messages: DatasetMessage[]
  metadata: {
    source: string
    mood: string
    stage: number
    topic: string
    quality: number
    original_response: string
    created_at: string
  }
}

interface DatasetStats {
  total: number
  by_source: Record<string, number>
  by_mood: Record<string, number>
  by_topic: Record<string, number>
  avg_quality: number
}

const MOODS = ['', 'happy', 'curious', 'playful', 'sleepy', 'excited', 'calm', 'confused', 'stubborn']
const STAGES = [1, 2, 3, 4, 5]
const SOURCES = ['manual', 'approved', 'correction', 'chat', 'synthetic', 'learned']

export function DatasetTab() {
  const [examples, setExamples] = useState<DatasetExample[]>([])
  const [stats, setStats] = useState<DatasetStats | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [showForm, setShowForm] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [filter, setFilter] = useState<{ source?: string; stage?: number }>({})

  const isReadOnly = filter.source === 'synthetic' || filter.source === 'learned'

  const fetchData = useCallback(async () => {
    try {
      const url = (filter.source === 'synthetic' || filter.source === 'learned')
        ? '/training/dataset?source=synthetic'
        : '/training/dataset'
      const data = await apiFetch<{ examples: DatasetExample[]; stats: DatasetStats }>(url)
      setExamples(data.examples)
      setStats(data.stats)
    } catch (err) {
      console.error('Failed to load dataset:', err)
    } finally {
      setIsLoading(false)
    }
  }, [filter.source])

  useEffect(() => {
    setIsLoading(true)
    fetchData()
  }, [fetchData])

  const handleDelete = async (id: string) => {
    try {
      await apiFetch(`/training/dataset/${id}`, { method: 'DELETE' })
      fetchData()
    } catch (err) {
      console.error('Failed to delete:', err)
    }
  }

  // Filter examples — synthetic/learned loads from server, others filter client-side
  const filtered = examples.filter((ex) => {
    if (filter.source && filter.source !== 'synthetic' && filter.source !== 'learned' && ex.metadata.source !== filter.source) return false
    if (filter.stage && ex.metadata.stage !== filter.stage) return false
    return true
  })

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64 text-text-muted">
        <p className="text-sm">Loading dataset...</p>
      </div>
    )
  }

  return (
    <div className="p-6 space-y-5">
      {/* Stats cards */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatCard label="Total Examples" value={stats.total} />
          <StatCard label="Avg Quality" value={stats.avg_quality ? stats.avg_quality.toFixed(1) : '—'} highlight />
          <StatCard
            label="Sources"
            value={Object.entries(stats.by_source || {})
              .map(([k, v]) => `${k}: ${v}`)
              .join(', ')}
            small
          />
          <StatCard
            label="Topics"
            value={Object.entries(stats.by_topic || {})
              .map(([k, v]) => `${k}: ${v}`)
              .join(', ')}
            small
          />
        </div>
      )}

      {/* Toolbar */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          {/* Source filter */}
          <select
            value={filter.source || ''}
            onChange={(e) => setFilter((f) => ({ ...f, source: e.target.value || undefined }))}
            className="bg-surface-tertiary text-text-primary text-xs rounded-md px-2 py-1.5 border border-border focus:border-accent focus:outline-none"
          >
            <option value="">All sources</option>
            {SOURCES.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>

          {/* Stage filter */}
          <select
            value={filter.stage || ''}
            onChange={(e) => setFilter((f) => ({ ...f, stage: e.target.value ? Number(e.target.value) : undefined }))}
            className="bg-surface-tertiary text-text-primary text-xs rounded-md px-2 py-1.5 border border-border focus:border-accent focus:outline-none"
          >
            <option value="">All stages</option>
            {STAGES.map((s) => (
              <option key={s} value={s}>Stage {s}</option>
            ))}
          </select>

          <span className="text-xs text-text-muted ml-2">
            {filtered.length} of {examples.length} examples
          </span>
        </div>

        {!isReadOnly && (
          <button
            onClick={() => { setShowForm(!showForm); setEditingId(null) }}
            className="px-3 py-1.5 rounded-md text-xs font-medium bg-accent text-white hover:bg-accent-hover transition-colors cursor-pointer"
          >
            {showForm ? 'Cancel' : '+ New Example'}
          </button>
        )}
      </div>

      {/* New Example Form */}
      {showForm && (
        <ExampleForm
          onSaved={() => {
            setShowForm(false)
            fetchData()
          }}
          onCancel={() => setShowForm(false)}
        />
      )}

      {/* Examples list */}
      <div className="space-y-3">
        {filtered.length === 0 && (
          <div className="text-center py-8 text-text-muted">
            <p className="text-3xl mb-2">📝</p>
            <p className="text-sm">
              {examples.length === 0
                ? 'No training examples yet. Create one or save from chat!'
                : 'No examples match the current filter.'}
            </p>
          </div>
        )}

        {filtered.map((ex) => (
          <ExampleCard
            key={ex.id}
            example={ex}
            isEditing={editingId === ex.id}
            onEdit={() => setEditingId(editingId === ex.id ? null : ex.id)}
            onDelete={() => handleDelete(ex.id)}
            onUpdated={() => {
              setEditingId(null)
              fetchData()
            }}
            readOnly={isReadOnly}
          />
        ))}
      </div>
    </div>
  )
}

// ─── Stat Card ──────────────────────────────────────────────────────

function StatCard({ label, value, highlight, small }: {
  label: string
  value: any
  highlight?: boolean
  small?: boolean
}) {
  return (
    <div className="bg-surface-secondary rounded-lg p-3 border border-border">
      <p className="text-xs text-text-muted mb-1">{label}</p>
      <p className={`font-semibold ${highlight ? 'text-success' : 'text-text-primary'} ${small ? 'text-xs' : 'text-sm'}`}>
        {value || '-'}
      </p>
    </div>
  )
}

// ─── Example Card ───────────────────────────────────────────────────

function ExampleCard({ example, isEditing, onEdit, onDelete, onUpdated, readOnly }: {
  example: DatasetExample
  isEditing: boolean
  onEdit: () => void
  onDelete: () => void
  onUpdated: () => void
  readOnly?: boolean
}) {
  const m = example.metadata
  const userMsg = example.messages.find((msg) => msg.role === 'user')
  const assistantMsg = example.messages.find((msg) => msg.role === 'assistant')

  return (
    <div className="bg-surface-secondary rounded-xl border border-border overflow-hidden">
      {/* Header */}
      <div className="px-4 py-2.5 flex items-center justify-between border-b border-border/50">
        <div className="flex items-center gap-2 flex-wrap">
          <SourceBadge source={m.source} />
          <span className="text-xs text-text-muted">Stage {m.stage}</span>
          {m.mood && <span className="text-xs px-1.5 py-0.5 rounded bg-accent/10 text-accent">{m.mood}</span>}
          {m.topic && <span className="text-xs px-1.5 py-0.5 rounded bg-surface-tertiary text-text-secondary">{m.topic}</span>}
          <QualityStars quality={m.quality} />
        </div>
        <div className="flex items-center gap-1">
          <span className="text-xs text-text-muted mr-2">
            {m.created_at ? new Date(m.created_at).toLocaleDateString() : ''}
          </span>
          {!readOnly && (
            <>
              <button
                onClick={onEdit}
                className="p-1 rounded text-text-muted hover:text-accent hover:bg-accent/10 transition-colors cursor-pointer"
                title="Edit"
              >
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                </svg>
              </button>
              <button
                onClick={onDelete}
                className="p-1 rounded text-text-muted hover:text-danger hover:bg-danger/10 transition-colors cursor-pointer"
                title="Delete"
              >
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                </svg>
              </button>
            </>
          )}
        </div>
      </div>

      {/* Messages preview */}
      <div className="px-4 py-3 space-y-2">
        {userMsg && (
          <div>
            <span className="text-xs font-medium text-accent">User:</span>
            <p className="text-sm text-text-primary mt-0.5 line-clamp-2">{userMsg.content}</p>
          </div>
        )}
        {assistantMsg && (
          <div>
            <span className="text-xs font-medium text-success">Assistant:</span>
            <p className="text-sm text-text-primary mt-0.5 line-clamp-3">{assistantMsg.content}</p>
          </div>
        )}
        {m.original_response && (
          <div>
            <span className="text-xs font-medium text-warning">Original (replaced):</span>
            <p className="text-xs text-text-muted mt-0.5 line-clamp-2">{m.original_response}</p>
          </div>
        )}
      </div>

      {/* Inline edit form */}
      {isEditing && (
        <EditForm example={example} onSaved={onUpdated} onCancel={onEdit} />
      )}
    </div>
  )
}

// ─── Source Badge ────────────────────────────────────────────────────

function SourceBadge({ source }: { source: string }) {
  const colors: Record<string, string> = {
    manual: 'bg-accent/15 text-accent',
    approved: 'bg-success/15 text-success',
    correction: 'bg-warning/15 text-warning',
    chat: 'bg-surface-tertiary text-text-secondary',
    synthetic: 'bg-purple-500/15 text-purple-400',
    learned: 'bg-emerald-500/15 text-emerald-400',
  }
  return (
    <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${colors[source] || colors.chat}`}>
      {source}
    </span>
  )
}

// ─── Quality Stars ──────────────────────────────────────────────────

function QualityStars({ quality }: { quality: number }) {
  return (
    <span className="flex items-center gap-0.5" title={`Quality: ${quality}/5`}>
      {[1, 2, 3, 4, 5].map((n) => (
        <span
          key={n}
          className={`text-xs ${n <= quality ? 'text-warning' : 'text-surface-tertiary'}`}
        >
          ★
        </span>
      ))}
    </span>
  )
}

// ─── New Example Form ───────────────────────────────────────────────

function ExampleForm({ onSaved, onCancel }: {
  onSaved: () => void
  onCancel: () => void
}) {
  const [userContent, setUserContent] = useState('')
  const [assistantContent, setAssistantContent] = useState('')
  const [mood, setMood] = useState('')
  const [stage, setStage] = useState(4)
  const [topic, setTopic] = useState('')
  const [quality, setQuality] = useState(5)
  const [saving, setSaving] = useState(false)

  const handleSubmit = async () => {
    if (!userContent.trim() || !assistantContent.trim()) return
    setSaving(true)
    try {
      const messages = [
        { role: 'user', content: userContent.trim() },
        { role: 'assistant', content: assistantContent.trim() },
      ]
      await apiFetch('/training/dataset', {
        method: 'POST',
        body: JSON.stringify({
          messages,
          source: 'manual',
          mood,
          stage,
          topic: topic.trim(),
          quality,
        }),
      })
      onSaved()
    } catch (err) {
      console.error('Failed to save example:', err)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="bg-surface-secondary rounded-xl border border-accent/30 p-4 space-y-3">
      <h3 className="text-sm font-semibold text-text-primary">New Training Example</h3>

      {/* User message */}
      <div>
        <label className="text-xs text-text-secondary mb-1 block">User Message</label>
        <textarea
          value={userContent}
          onChange={(e) => setUserContent(e.target.value)}
          placeholder="What the user says..."
          rows={2}
          className="w-full bg-surface text-text-primary text-sm rounded-lg p-2.5 border border-border focus:border-accent focus:outline-none resize-y"
        />
      </div>

      {/* Assistant message */}
      <div>
        <label className="text-xs text-text-secondary mb-1 block">Assistant Response</label>
        <textarea
          value={assistantContent}
          onChange={(e) => setAssistantContent(e.target.value)}
          placeholder="How the pet should respond..."
          rows={3}
          className="w-full bg-surface text-text-primary text-sm rounded-lg p-2.5 border border-border focus:border-accent focus:outline-none resize-y"
        />
      </div>

      {/* Metadata row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div>
          <label className="text-xs text-text-secondary mb-1 block">Mood</label>
          <select
            value={mood}
            onChange={(e) => setMood(e.target.value)}
            className="w-full bg-surface text-text-primary text-xs rounded-md px-2 py-1.5 border border-border focus:border-accent focus:outline-none"
          >
            {MOODS.map((m) => (
              <option key={m} value={m}>{m || '(none)'}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="text-xs text-text-secondary mb-1 block">Stage</label>
          <select
            value={stage}
            onChange={(e) => setStage(Number(e.target.value))}
            className="w-full bg-surface text-text-primary text-xs rounded-md px-2 py-1.5 border border-border focus:border-accent focus:outline-none"
          >
            {STAGES.map((s) => (
              <option key={s} value={s}>Stage {s}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="text-xs text-text-secondary mb-1 block">Topic</label>
          <input
            type="text"
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            placeholder="e.g. greeting, memory..."
            className="w-full bg-surface text-text-primary text-xs rounded-md px-2 py-1.5 border border-border focus:border-accent focus:outline-none"
          />
        </div>
        <div>
          <label className="text-xs text-text-secondary mb-1 block">Quality ({quality}/5)</label>
          <input
            type="range"
            min={1}
            max={5}
            value={quality}
            onChange={(e) => setQuality(Number(e.target.value))}
            className="w-full accent-accent mt-1"
          />
        </div>
      </div>

      {/* Actions */}
      <div className="flex justify-end gap-2 pt-1">
        <button
          onClick={onCancel}
          className="px-3 py-1.5 rounded-md text-xs font-medium bg-surface-tertiary text-text-secondary hover:text-text-primary cursor-pointer"
        >
          Cancel
        </button>
        <button
          onClick={handleSubmit}
          disabled={saving || !userContent.trim() || !assistantContent.trim()}
          className="px-3 py-1.5 rounded-md text-xs font-medium bg-accent text-white hover:bg-accent-hover cursor-pointer disabled:opacity-40"
        >
          {saving ? 'Saving...' : 'Save Example'}
        </button>
      </div>
    </div>
  )
}

// ─── Edit Form (inline in card) ─────────────────────────────────────

function EditForm({ example, onSaved, onCancel }: {
  example: DatasetExample
  onSaved: () => void
  onCancel: () => void
}) {
  const userMsg = example.messages.find((m) => m.role === 'user')
  const assistantMsg = example.messages.find((m) => m.role === 'assistant')

  const [userContent, setUserContent] = useState(userMsg?.content || '')
  const [assistantContent, setAssistantContent] = useState(assistantMsg?.content || '')
  const [mood, setMood] = useState(example.metadata.mood)
  const [stage, setStage] = useState(example.metadata.stage)
  const [topic, setTopic] = useState(example.metadata.topic)
  const [quality, setQuality] = useState(example.metadata.quality)
  const [saving, setSaving] = useState(false)

  const handleSubmit = async () => {
    setSaving(true)
    try {
      const messages = [
        { role: 'user', content: userContent.trim() },
        { role: 'assistant', content: assistantContent.trim() },
      ]
      await apiFetch(`/training/dataset/${example.id}`, {
        method: 'PUT',
        body: JSON.stringify({
          messages,
          metadata: { mood, stage, topic: topic.trim(), quality },
        }),
      })
      onSaved()
    } catch (err) {
      console.error('Failed to update example:', err)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="px-4 py-3 border-t border-border bg-surface-tertiary/30 space-y-3">
      <div>
        <label className="text-xs text-text-secondary mb-1 block">User Message</label>
        <textarea
          value={userContent}
          onChange={(e) => setUserContent(e.target.value)}
          rows={2}
          className="w-full bg-surface text-text-primary text-sm rounded-md p-2 border border-border focus:border-accent focus:outline-none resize-y"
        />
      </div>
      <div>
        <label className="text-xs text-text-secondary mb-1 block">Assistant Response</label>
        <textarea
          value={assistantContent}
          onChange={(e) => setAssistantContent(e.target.value)}
          rows={3}
          className="w-full bg-surface text-text-primary text-sm rounded-md p-2 border border-border focus:border-accent focus:outline-none resize-y"
        />
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div>
          <label className="text-xs text-text-secondary mb-1 block">Mood</label>
          <select value={mood} onChange={(e) => setMood(e.target.value)}
            className="w-full bg-surface text-text-primary text-xs rounded-md px-2 py-1.5 border border-border focus:border-accent focus:outline-none">
            {MOODS.map((m) => <option key={m} value={m}>{m || '(none)'}</option>)}
          </select>
        </div>
        <div>
          <label className="text-xs text-text-secondary mb-1 block">Stage</label>
          <select value={stage} onChange={(e) => setStage(Number(e.target.value))}
            className="w-full bg-surface text-text-primary text-xs rounded-md px-2 py-1.5 border border-border focus:border-accent focus:outline-none">
            {STAGES.map((s) => <option key={s} value={s}>Stage {s}</option>)}
          </select>
        </div>
        <div>
          <label className="text-xs text-text-secondary mb-1 block">Topic</label>
          <input type="text" value={topic} onChange={(e) => setTopic(e.target.value)}
            className="w-full bg-surface text-text-primary text-xs rounded-md px-2 py-1.5 border border-border focus:border-accent focus:outline-none" />
        </div>
        <div>
          <label className="text-xs text-text-secondary mb-1 block">Quality ({quality}/5)</label>
          <input type="range" min={1} max={5} value={quality}
            onChange={(e) => setQuality(Number(e.target.value))} className="w-full accent-accent mt-1" />
        </div>
      </div>
      <div className="flex justify-end gap-2">
        <button onClick={onCancel}
          className="px-2.5 py-1 rounded text-xs text-text-secondary hover:text-text-primary bg-surface-tertiary cursor-pointer">
          Cancel
        </button>
        <button onClick={handleSubmit} disabled={saving}
          className="px-2.5 py-1 rounded text-xs text-white bg-accent hover:bg-accent-hover cursor-pointer disabled:opacity-40">
          {saving ? 'Saving...' : 'Save Changes'}
        </button>
      </div>
    </div>
  )
}
