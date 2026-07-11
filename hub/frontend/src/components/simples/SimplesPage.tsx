import { useEffect, useState } from 'react'
import { apiFetch } from '../../lib/api'
import { fmtRelative } from '../overseer/shared'

// Simples on the desktop (2026-07-11, Tory's ask): a READ-ONLY mirror
// of the phone's liquid planner. The phone pushes a snapshot blob on
// every home sync; editing (goals, reflow, block states) stays on the
// phone where the reflow engine lives. Display state, not corpus.

interface Goal {
  id: number
  title: string
  project_tag: string
  target_hours: number
  period_start: string
  period_end: string
  status: string
}

interface Block {
  id: number
  day: string
  start_min: number
  end_min: number
  kind: string
  title: string
  goal_id: number | null
  state: string
  pinned: number
}

interface Snapshot {
  goals: Goal[]
  blocks: Block[]
  from: string
  to: string
  received_at: string
}

function fmtMin(m: number): string {
  const h = Math.floor(m / 60)
  const mm = m % 60
  const h12 = ((h + 11) % 12) + 1
  return `${h12}${mm ? ':' + String(mm).padStart(2, '0') : ''}${h < 12 ? 'a' : 'p'}`
}

function localDay(offsetDays: number): string {
  const d = new Date(Date.now() + offsetDays * 86400000)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

function dayLabel(day: string, today: string): string {
  if (day === today) return 'Today'
  if (day === localDay(1)) return 'Tomorrow'
  const d = new Date(day + 'T12:00:00')
  return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })
}

const STATE_STYLE: Record<string, string> = {
  planned: 'border-border bg-surface-tertiary/40 text-text-primary',
  done: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300',
  missed: 'border-red-500/30 bg-red-500/5 text-text-muted line-through',
}

const KIND_DOT: Record<string, string> = {
  goal: 'bg-purple-400',
  anchor: 'bg-sky-400',
  routine: 'bg-amber-400',
}

export function SimplesPage() {
  const [snap, setSnap] = useState<Snapshot | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    apiFetch<{ ok: boolean; snapshot?: Snapshot | null; error?: string }>(
      '/overseer/simples/snapshot',
    )
      .then((r) => {
        if (!r.ok) setError(r.error || 'snapshot fetch failed')
        else setSnap(r.snapshot ?? null)
      })
      .catch((e) => setError(e?.message || String(e)))
      .finally(() => setLoaded(true))
  }, [])

  const today = localDay(0)

  if (!loaded) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-sm text-text-muted animate-pulse">Loading planner…</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex-1 p-8">
        <div className="text-sm text-red-400">Simples mirror unavailable: {error}</div>
      </div>
    )
  }

  if (!snap || (snap.goals.length === 0 && snap.blocks.length === 0)) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-3 p-8">
        <div className="text-4xl">📅</div>
        <div className="text-sm text-text-secondary max-w-md text-center">
          No plan mirrored yet. Simples lives on your phone — open the app on
          home WiFi and sync, and the plan appears here. Create goals and
          blocks from the phone&apos;s Simples tab or by voice.
        </div>
      </div>
    )
  }

  // Group upcoming blocks by day (today onward; the snapshot also
  // carries the past week for context but the mirror leads with now).
  const upcoming = new Map<string, Block[]>()
  for (const b of snap.blocks) {
    if (b.day < today) continue
    const arr = upcoming.get(b.day) || []
    arr.push(b)
    upcoming.set(b.day, arr)
  }
  const days = [...upcoming.keys()].sort().slice(0, 7)

  // Goal progress from the snapshot's own blocks (done minutes within
  // the mirrored window; the phone's credit engine is authoritative,
  // this is the at-a-glance view).
  const doneMin = new Map<number, number>()
  for (const b of snap.blocks) {
    if (b.state === 'done' && b.goal_id) {
      doneMin.set(b.goal_id, (doneMin.get(b.goal_id) || 0) + (b.end_min - b.start_min))
    }
  }

  const activeGoals = snap.goals.filter((g) => g.status === 'active')

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-4xl mx-auto space-y-6">
        <div className="flex items-baseline justify-between flex-wrap gap-2">
          <h2 className="text-lg font-semibold text-text-primary">Simples</h2>
          <div className="text-xs text-text-muted">
            Read-only mirror of your phone planner · synced {fmtRelative(
              snap.received_at.includes('T')
                ? snap.received_at
                : snap.received_at.replace(' ', 'T') + 'Z')}
          </div>
        </div>

        {activeGoals.length > 0 && (
          <section className="rounded-lg border border-border bg-surface-secondary p-4">
            <h3 className="text-sm font-semibold text-text-primary mb-3">Goals</h3>
            <div className="space-y-3">
              {activeGoals.map((g) => {
                const done = (doneMin.get(g.id) || 0) / 60
                const pct = g.target_hours > 0
                  ? Math.min(100, Math.round((done / g.target_hours) * 100))
                  : 0
                return (
                  <div key={g.id}>
                    <div className="flex justify-between text-xs mb-1">
                      <span className="font-medium text-text-primary">
                        {g.title}
                        {g.project_tag ? (
                          <span className="text-text-muted"> · {g.project_tag}</span>
                        ) : null}
                      </span>
                      <span className="text-text-muted">
                        {done.toFixed(1)}h / {g.target_hours}h in window
                      </span>
                    </div>
                    <div className="h-1.5 rounded-full bg-surface-tertiary overflow-hidden">
                      <div
                        className="h-full rounded-full bg-accent"
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </div>
                )
              })}
            </div>
          </section>
        )}

        {days.length === 0 ? (
          <div className="text-sm text-text-muted">
            No upcoming blocks in the mirrored window.
          </div>
        ) : (
          days.map((day) => (
            <section
              key={day}
              className="rounded-lg border border-border bg-surface-secondary p-4"
            >
              <h3 className="text-sm font-semibold text-text-primary mb-3">
                {dayLabel(day, today)}
                <span className="text-xs font-normal text-text-muted ml-2">{day}</span>
              </h3>
              <div className="space-y-1.5">
                {(upcoming.get(day) || []).map((b) => (
                  <div
                    key={b.id}
                    className={`flex items-center gap-3 rounded-md border px-3 py-1.5 text-xs ${
                      STATE_STYLE[b.state] || STATE_STYLE.planned
                    }`}
                  >
                    <span
                      className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                        KIND_DOT[b.kind] || 'bg-text-muted'
                      }`}
                    />
                    <span className="w-24 shrink-0 text-text-muted">
                      {fmtMin(b.start_min)}–{fmtMin(b.end_min)}
                    </span>
                    <span className="flex-1 truncate">{b.title || b.kind}</span>
                    {b.pinned ? <span title="Pinned">📌</span> : null}
                    {b.state === 'done' ? <span>✓</span> : null}
                  </div>
                ))}
              </div>
            </section>
          ))
        )}

        <div className="text-[11px] text-text-muted">
          Edit goals and blocks on the phone (Simples tab or by voice); the
          reflow engine lives there. This mirror updates on every home sync.
        </div>
      </div>
    </div>
  )
}
