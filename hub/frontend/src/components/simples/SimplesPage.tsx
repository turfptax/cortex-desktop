import { useEffect, useMemo, useState } from 'react'
import { apiFetch } from '../../lib/api'
import { fmtRelative } from '../overseer/shared'

// Simples on the desktop (2026-07-11): a READ-ONLY mirror of the
// phone's liquid planner with Day / Week / Month views. The phone
// pushes a snapshot blob on every home sync; editing (goals, reflow,
// block states) stays on the phone where the reflow engine lives.
//
// Visual language mirrors the phone: blocks are boats, downtime is
// water (a recessive full-width wash, never a loud color). Anchors
// are external calendar events and render OUTLINED (dashed) so their
// identity is carried by shape, not hue alone. Kind hues follow the
// phone's entity colors, darkened to pass the dark-surface palette
// checks (lightness band, CVD separation, contrast) — validated with
// the dataviz six-check script on 2026-07-11.

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

type ViewMode = 'day' | 'week' | 'month' | 'year'

// Entity colors (validated categorical set; work + downtime are
// deliberately recessive neutrals and sit outside the loud set).
const KIND_COLOR: Record<string, string> = {
  goal: '#7c5cff',
  chore: '#d97706',
  adl: '#c026d3',
  anchor: '#b45309',
  work: '#8b949e',
  downtime: '#3d4a5c',
}
const KIND_LABEL: Record<string, string> = {
  goal: 'Goal',
  work: 'Work',
  adl: 'ADL',
  chore: 'Chore',
  anchor: 'Calendar',
  downtime: 'Downtime',
}
const LEGEND_ORDER = ['goal', 'work', 'adl', 'chore', 'anchor', 'downtime']

const kindColor = (k: string) => KIND_COLOR[k] || '#8b949e'

// ── date helpers (local-day strings, matching the phone) ───────────

function localDay(offsetDays: number, base?: string): string {
  const d = base ? new Date(base + 'T12:00:00') : new Date()
  d.setDate(d.getDate() + offsetDays)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

function weekStart(day: string): string {
  const d = new Date(day + 'T12:00:00')
  const dow = (d.getDay() + 6) % 7 // Monday = 0
  return localDay(-dow, day)
}

function fmtMin(m: number): string {
  const h = Math.floor(m / 60)
  const mm = m % 60
  const h12 = ((h + 11) % 12) + 1
  return `${h12}${mm ? ':' + String(mm).padStart(2, '0') : ''}${h < 12 ? 'am' : 'pm'}`
}

function dayTitle(day: string, today: string): string {
  if (day === today) return 'Today'
  if (day === localDay(1, today)) return 'Tomorrow'
  if (day === localDay(-1, today)) return 'Yesterday'
  return new Date(day + 'T12:00:00').toLocaleDateString(undefined, {
    weekday: 'long', month: 'short', day: 'numeric',
  })
}

// ── lane layout: overlapping non-downtime blocks share the width ───

interface Laned extends Block {
  lane: number
  lanes: number
}

function assignLanes(blocks: Block[]): Laned[] {
  const sorted = [...blocks].sort(
    (a, b) => a.start_min - b.start_min || b.end_min - a.end_min)
  const laneEnds: number[] = []
  const out: Laned[] = []
  for (const b of sorted) {
    let lane = laneEnds.findIndex((end) => end <= b.start_min)
    if (lane === -1) {
      lane = laneEnds.length
      laneEnds.push(b.end_min)
    } else {
      laneEnds[lane] = b.end_min
    }
    out.push({ ...b, lane, lanes: 1 })
  }
  // Lane count per overlap cluster so widths only shrink where needed.
  for (const b of out) {
    let overlapMax = b.lane
    for (const o of out) {
      if (o.start_min < b.end_min && o.end_min > b.start_min) {
        overlapMax = Math.max(overlapMax, o.lane)
      }
    }
    b.lanes = overlapMax + 1
  }
  return out
}

// ── page ────────────────────────────────────────────────────────────

export function SimplesPage() {
  const [snap, setSnap] = useState<Snapshot | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState('')
  const today = localDay(0)
  const [view, setView] = useState<ViewMode>(() => {
    const v = localStorage.getItem('cortex.simples.view')
    return v === 'week' || v === 'month' || v === 'year' ? v : 'day'
  })
  const [anchorDay, setAnchorDay] = useState<string>(today)

  // Year view: credited effort by day (the phone's time log, synced
  // to the Pi as time_entries). One fetch covers every year; the map
  // is merged with done blocks from the mirrored window below.
  const [logCredit, setLogCredit] = useState<Map<string, number>>(new Map())
  const [creditState, setCreditState] = useState<'idle' | 'loading' | 'ready'>('idle')
  useEffect(() => {
    if (view !== 'year' || creditState !== 'idle') return
    setCreditState('loading')
    apiFetch<{ rows?: {
      started_at?: string; local_started_at?: string;
      duration_minutes?: number;
    }[] }>(
      '/data/query',
      {
        method: 'POST',
        body: JSON.stringify({
          table: 'time_entries', limit: 5000, order_by: 'started_at DESC',
        }),
      },
    )
      .then((r) => {
        const m = new Map<string, number>()
        const p = (n: number) => String(n).padStart(2, '0')
        for (const row of r.rows || []) {
          // started_at is UTC; local_started_at is its local-with-
          // offset twin and gives the correct local day directly.
          let day = (row.local_started_at || '').slice(0, 10)
          if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) {
            if (!row.started_at) continue
            const d = new Date(String(row.started_at).replace(' ', 'T') + 'Z')
            if (isNaN(d.getTime())) continue
            day = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
          }
          m.set(day, (m.get(day) || 0) + (Number(row.duration_minutes) || 0))
        }
        setLogCredit(m)
      })
      .catch(() => { /* year view just renders empty */ })
      .finally(() => setCreditState('ready'))
  }, [view, creditState])

  useEffect(() => {
    localStorage.setItem('cortex.simples.view', view)
  }, [view])

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

  const byDay = useMemo(() => {
    const m = new Map<string, Block[]>()
    for (const b of snap?.blocks || []) {
      const arr = m.get(b.day) || []
      arr.push(b)
      m.set(b.day, arr)
    }
    return m
  }, [snap])

  // Mirror the phone's creditedAllByDay: logged time_entries minutes,
  // max'd with done-block minutes where the mirrored window has them.
  const credit = useMemo(() => {
    const m = new Map(logCredit)
    for (const [day, blocks] of byDay) {
      const done = blocks
        .filter((b) => b.state === 'done' && b.kind !== 'downtime')
        .reduce((s, b) => s + (b.end_min - b.start_min), 0)
      if (done > 0) m.set(day, Math.max(m.get(day) || 0, done))
    }
    return m
  }, [logCredit, byDay])

  const step = (dir: number) => {
    const n = view === 'day' ? 1 : view === 'week' ? 7 : 0
    if (view === 'month') {
      const d = new Date(anchorDay + 'T12:00:00')
      d.setMonth(d.getMonth() + dir, 15)
      const p = (x: number) => String(x).padStart(2, '0')
      setAnchorDay(`${d.getFullYear()}-${p(d.getMonth() + 1)}-15`)
    } else if (view === 'year') {
      const y = Number(anchorDay.slice(0, 4)) + dir
      setAnchorDay(`${y}-07-01`)
    } else {
      setAnchorDay(localDay(dir * n, anchorDay))
    }
  }

  if (!loaded) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-sm text-text-muted animate-pulse">Loading planner…</div>
      </div>
    )
  }
  if (error) {
    return (
      <div className="flex-1 p-8 text-sm text-red-400">
        Simples mirror unavailable: {error}
      </div>
    )
  }
  if (!snap || (snap.goals.length === 0 && snap.blocks.length === 0)) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-3 p-8">
        <div className="text-4xl">📅</div>
        <div className="text-sm text-text-secondary max-w-md text-center">
          No plan mirrored yet. Simples lives on your phone — open the app
          on home WiFi and sync, and the plan appears here.
        </div>
      </div>
    )
  }

  const syncedAgo = fmtRelative(
    snap.received_at.includes('T')
      ? snap.received_at
      : snap.received_at.replace(' ', 'T') + 'Z')
  const staleSync =
    Date.now() - new Date(
      snap.received_at.replace(' ', 'T') + 'Z').getTime() > 86_400_000

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-5xl mx-auto space-y-4">
        {/* Header: view toggle + date nav + sync badge */}
        <div className="flex items-center gap-3 flex-wrap">
          <h2 className="text-lg font-semibold text-text-primary mr-1">Simples</h2>
          <div className="flex rounded-md border border-border overflow-hidden">
            {(['day', 'week', 'month', 'year'] as ViewMode[]).map((v) => (
              <button
                key={v}
                onClick={() => setView(v)}
                className={`px-3 py-1 text-xs font-medium cursor-pointer capitalize ${
                  view === v
                    ? 'bg-accent/20 text-accent'
                    : 'text-text-muted hover:text-text-primary'
                }`}
              >
                {v}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-1">
            <NavBtn label="‹" onClick={() => step(-1)} />
            <button
              onClick={() => setAnchorDay(today)}
              className="px-2.5 py-1 rounded text-xs text-text-secondary hover:text-text-primary border border-border cursor-pointer"
            >
              Today
            </button>
            <NavBtn label="›" onClick={() => step(1)} />
          </div>
          <div className="ml-auto text-xs text-text-muted">
            {staleSync ? '⚠ ' : ''}phone synced {syncedAgo}
          </div>
        </div>

        <GoalsCard goals={snap.goals} blocks={snap.blocks} />

        {view === 'day' && (
          <DayView
            day={anchorDay}
            today={today}
            blocks={byDay.get(anchorDay) || []}
            inWindow={anchorDay >= snap.from && anchorDay <= snap.to}
          />
        )}
        {view === 'week' && (
          <WeekView
            start={weekStart(anchorDay)}
            today={today}
            byDay={byDay}
            onPickDay={(d) => { setAnchorDay(d); setView('day') }}
          />
        )}
        {view === 'month' && (
          <MonthView
            anchorDay={anchorDay}
            today={today}
            byDay={byDay}
            onPickDay={(d) => { setAnchorDay(d); setView('day') }}
          />
        )}
        {view === 'year' && (
          <YearView
            year={Number(anchorDay.slice(0, 4))}
            today={today}
            credit={credit}
            loading={creditState === 'loading'}
            onPickDay={(d) => { setAnchorDay(d); setView('day') }}
          />
        )}

        {/* Legend: identity is never color-alone (labels + dots). */}
        <div className="flex items-center gap-4 flex-wrap text-[11px] text-text-muted">
          {LEGEND_ORDER.map((k) => (
            <span key={k} className="flex items-center gap-1.5">
              <span
                className="w-2.5 h-2.5 rounded-sm"
                style={
                  k === 'anchor'
                    ? { border: `1.5px dashed ${kindColor(k)}` }
                    : { background: kindColor(k) }
                }
              />
              {KIND_LABEL[k]}
            </span>
          ))}
          <span className="flex items-center gap-1.5">✓ done</span>
          <span className="flex items-center gap-1.5 line-through">missed</span>
          <span className="ml-auto">
            Read-only mirror — edit on the phone (Simples tab or by voice).
          </span>
        </div>
      </div>
    </div>
  )
}

function NavBtn({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="w-7 h-7 rounded border border-border text-text-secondary hover:text-text-primary cursor-pointer text-sm"
      aria-label={label === '‹' ? 'Previous' : 'Next'}
    >
      {label}
    </button>
  )
}

// ── goals ───────────────────────────────────────────────────────────

function GoalsCard({ goals, blocks }: { goals: Goal[]; blocks: Block[] }) {
  const active = goals.filter((g) => g.status === 'active')
  if (active.length === 0) return null
  const doneMin = new Map<number, number>()
  for (const b of blocks) {
    if (b.state === 'done' && b.goal_id) {
      doneMin.set(b.goal_id,
        (doneMin.get(b.goal_id) || 0) + (b.end_min - b.start_min))
    }
  }
  return (
    <section className="rounded-lg border border-border bg-surface-secondary p-4">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-x-8 gap-y-3">
        {active.map((g) => {
          const done = (doneMin.get(g.id) || 0) / 60
          const pct = g.target_hours > 0
            ? Math.min(100, Math.round((done / g.target_hours) * 100))
            : 0
          return (
            <div key={g.id}>
              <div className="flex justify-between text-xs mb-1 gap-2">
                <span className="font-medium text-text-primary truncate">
                  {g.title}
                  {g.project_tag ? (
                    <span className="text-text-muted"> · {g.project_tag}</span>
                  ) : null}
                </span>
                <span className="text-text-muted shrink-0">
                  {done.toFixed(1)} / {g.target_hours}h
                </span>
              </div>
              <div className="h-1.5 rounded-full bg-surface-tertiary overflow-hidden">
                <div
                  className="h-full rounded-full"
                  style={{ width: `${pct}%`, background: KIND_COLOR.goal }}
                />
              </div>
            </div>
          )
        })}
      </div>
    </section>
  )
}

// ── day view: the timeline ──────────────────────────────────────────

const PX_PER_MIN = 1.05

function DayView({ day, today, blocks, inWindow }: {
  day: string
  today: string
  blocks: Block[]
  inWindow: boolean
}) {
  const water = blocks.filter((b) => b.kind === 'downtime')
  const boats = assignLanes(blocks.filter((b) => b.kind !== 'downtime'))
  const all = blocks
  const startMin = Math.min(6 * 60, ...all.map((b) => b.start_min))
  const endMin = Math.max(22 * 60, ...all.map((b) => b.end_min))
  const height = (endMin - startMin) * PX_PER_MIN
  const y = (m: number) => (m - startMin) * PX_PER_MIN
  const hours: number[] = []
  for (let h = Math.ceil(startMin / 60); h * 60 <= endMin; h++) hours.push(h)
  const now = new Date()
  const nowMin = now.getHours() * 60 + now.getMinutes()
  const showNow = day === today && nowMin >= startMin && nowMin <= endMin

  return (
    <section className="rounded-lg border border-border bg-surface-secondary p-4">
      <h3 className="text-sm font-semibold text-text-primary mb-3">
        {dayTitle(day, today)}
        <span className="text-xs font-normal text-text-muted ml-2">{day}</span>
      </h3>
      {blocks.length === 0 ? (
        <div className="text-xs text-text-muted py-8 text-center">
          {inWindow
            ? 'No blocks planned this day.'
            : 'Outside the mirrored window (the phone mirrors -7 to +14 days).'}
        </div>
      ) : (
        <div className="relative" style={{ height }}>
          {/* hour grid (recessive) */}
          {hours.map((h) => (
            <div
              key={h}
              className="absolute left-0 right-0 border-t border-border/30"
              style={{ top: y(h * 60) }}
            >
              <span className="absolute -top-2 left-0 text-[10px] text-text-muted w-11">
                {fmtMin(h * 60)}
              </span>
            </div>
          ))}
          {/* downtime = water: recessive full-width washes */}
          {water.map((b) => (
            <div
              key={b.id}
              title={`${b.title || 'Downtime'} · ${fmtMin(b.start_min)}–${fmtMin(b.end_min)}`}
              className="absolute rounded"
              style={{
                top: y(b.start_min),
                height: Math.max(6, (b.end_min - b.start_min) * PX_PER_MIN - 2),
                left: 52,
                right: 0,
                background: 'rgba(61,74,92,0.16)',
              }}
            />
          ))}
          {/* boats: laned blocks */}
          {boats.map((b) => {
            const c = kindColor(b.kind)
            const isAnchor = b.kind === 'anchor'
            const missed = b.state === 'missed' || b.state === 'skipped'
            const done = b.state === 'done'
            const laneW = 100 / b.lanes
            const h = Math.max(16, (b.end_min - b.start_min) * PX_PER_MIN - 2)
            return (
              <div
                key={b.id}
                title={`${b.title || b.kind} · ${fmtMin(b.start_min)}–${fmtMin(b.end_min)} · ${b.state}`}
                className={`absolute rounded-md px-2 py-0.5 overflow-hidden text-xs ${
                  missed ? 'opacity-45' : ''
                }`}
                style={{
                  top: y(b.start_min),
                  height: h,
                  left: `calc(52px + ${b.lane * laneW}% - ${b.lane * laneW * 0.52}px)`,
                  width: `calc(${laneW}% - ${laneW * 0.52}px - 4px)`,
                  background: isAnchor ? 'transparent' : `${c}21`,
                  border: isAnchor ? `1.5px dashed ${c}` : undefined,
                  borderLeft: isAnchor ? undefined : `3px solid ${c}`,
                }}
              >
                <div className={`truncate font-medium text-text-primary ${
                  missed ? 'line-through' : ''
                }`}>
                  {done ? '✓ ' : ''}{b.pinned ? '📌 ' : ''}
                  {b.title || KIND_LABEL[b.kind] || b.kind}
                </div>
                {h >= 34 && (
                  <div className="text-[10px] text-text-muted">
                    {fmtMin(b.start_min)}–{fmtMin(b.end_min)}
                  </div>
                )}
              </div>
            )
          })}
          {/* now line */}
          {showNow && (
            <div
              className="absolute left-11 right-0 z-10 pointer-events-none"
              style={{ top: y(nowMin) }}
            >
              <div className="border-t-2 border-red-400/80" />
              <div className="absolute -left-1 -top-[5px] w-2 h-2 rounded-full bg-red-400" />
            </div>
          )}
        </div>
      )}
    </section>
  )
}

// ── week view: 7 mini timelines ─────────────────────────────────────

function WeekView({ start, today, byDay, onPickDay }: {
  start: string
  today: string
  byDay: Map<string, Block[]>
  onPickDay: (d: string) => void
}) {
  const days = Array.from({ length: 7 }, (_, i) => localDay(i, start))
  const START = 6 * 60
  const END = 23 * 60
  const H = 420
  const y = (m: number) =>
    (Math.min(Math.max(m, START), END) - START) / (END - START) * H

  return (
    <section className="rounded-lg border border-border bg-surface-secondary p-4">
      <div className="grid grid-cols-7 gap-2">
        {days.map((d) => {
          const blocks = byDay.get(d) || []
          const water = blocks.filter((b) => b.kind === 'downtime')
          const boats = assignLanes(blocks.filter((b) => b.kind !== 'downtime'))
          const label = new Date(d + 'T12:00:00').toLocaleDateString(
            undefined, { weekday: 'short' })
          return (
            <div key={d} className="min-w-0">
              <button
                onClick={() => onPickDay(d)}
                title={`Open ${d}`}
                className={`w-full text-center text-xs pb-1.5 cursor-pointer rounded ${
                  d === today
                    ? 'text-accent font-semibold'
                    : 'text-text-muted hover:text-text-primary'
                }`}
              >
                {label} <span className="opacity-70">{d.slice(8)}</span>
              </button>
              <div
                className="relative rounded bg-surface-tertiary/30 overflow-hidden"
                style={{ height: H }}
              >
                {water.map((b) => (
                  <div
                    key={b.id}
                    className="absolute left-0 right-0"
                    style={{
                      top: y(b.start_min),
                      height: Math.max(4, y(b.end_min) - y(b.start_min)),
                      background: 'rgba(61,74,92,0.18)',
                    }}
                  />
                ))}
                {boats.map((b) => {
                  const c = kindColor(b.kind)
                  const isAnchor = b.kind === 'anchor'
                  const missed = b.state === 'missed' || b.state === 'skipped'
                  const bh = Math.max(10, y(b.end_min) - y(b.start_min) - 1)
                  const laneW = 100 / b.lanes
                  return (
                    <div
                      key={b.id}
                      title={`${b.title || b.kind} · ${fmtMin(b.start_min)}–${fmtMin(b.end_min)} · ${b.state}`}
                      className={`absolute rounded-sm px-1 overflow-hidden ${
                        missed ? 'opacity-40' : ''
                      }`}
                      style={{
                        top: y(b.start_min),
                        height: bh,
                        left: `${b.lane * laneW}%`,
                        width: `calc(${laneW}% - 2px)`,
                        background: isAnchor ? 'transparent' : `${c}30`,
                        border: isAnchor ? `1px dashed ${c}` : undefined,
                        borderLeft: isAnchor ? undefined : `2px solid ${c}`,
                      }}
                    >
                      {bh >= 16 && (
                        <span className={`text-[9px] text-text-primary truncate block ${
                          missed ? 'line-through' : ''
                        }`}>
                          {b.state === 'done' ? '✓' : ''}{b.title || b.kind}
                        </span>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          )
        })}
      </div>
    </section>
  )
}

// ── month view: planned-hours heat calendar ─────────────────────────

function MonthView({ anchorDay, today, byDay, onPickDay }: {
  anchorDay: string
  today: string
  byDay: Map<string, Block[]>
  onPickDay: (d: string) => void
}) {
  const first = anchorDay.slice(0, 8) + '01'
  const monthLabel = new Date(first + 'T12:00:00').toLocaleDateString(
    undefined, { month: 'long', year: 'numeric' })
  const gridStart = weekStart(first)
  const cells = Array.from({ length: 42 }, (_, i) => localDay(i, gridStart))
  const month = anchorDay.slice(0, 7)

  // Sequential encoding: one hue (the accent), alpha by planned
  // non-downtime hours. Light -> dark = less -> more planned.
  const heat = (mins: number): string => {
    if (mins <= 0) return 'transparent'
    const a = Math.min(0.42, 0.08 + (mins / 480) * 0.34)
    return `rgba(124,92,255,${a.toFixed(2)})`
  }

  return (
    <section className="rounded-lg border border-border bg-surface-secondary p-4">
      <h3 className="text-sm font-semibold text-text-primary mb-3">{monthLabel}</h3>
      <div className="grid grid-cols-7 gap-1.5 text-center text-[10px] text-text-muted mb-1">
        {['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((w) => (
          <div key={w}>{w}</div>
        ))}
      </div>
      <div className="grid grid-cols-7 gap-1.5">
        {cells.map((d) => {
          const inMonth = d.slice(0, 7) === month
          const blocks = (byDay.get(d) || []).filter(
            (b) => b.kind !== 'downtime')
          const planned = blocks.reduce(
            (s, b) => s + (b.end_min - b.start_min), 0)
          const done = blocks.filter((b) => b.state === 'done').length
          return (
            <button
              key={d}
              onClick={() => onPickDay(d)}
              title={blocks.length
                ? `${d}: ${(planned / 60).toFixed(1)}h planned, ${done}/${blocks.length} done`
                : d}
              className={`relative h-16 rounded-md border text-left p-1.5 cursor-pointer transition-colors ${
                d === today
                  ? 'border-accent'
                  : 'border-border/50 hover:border-border'
              } ${inMonth ? '' : 'opacity-35'}`}
              style={{ background: heat(planned) }}
            >
              <span className={`text-[11px] ${
                d === today ? 'text-accent font-semibold' : 'text-text-secondary'
              }`}>
                {Number(d.slice(8))}
              </span>
              {blocks.length > 0 && (
                <span className="absolute bottom-1 left-1.5 right-1.5 text-[9px] text-text-muted truncate">
                  {(planned / 60).toFixed(1)}h{done ? ` · ${done}✓` : ''}
                </span>
              )}
            </button>
          )
        })}
      </div>
      <div className="text-[10px] text-text-muted mt-2">
        Shading = planned hours (excluding downtime); days outside the
        mirrored window are simply empty.
      </div>
    </section>
  )
}

// ── year view: the year as texture (mirrors the phone) ─────────────
// 12 month rows, each day a sliver whose brightness is that day's
// credited effort (logged time). No numbers on the grid; shape over
// score. Same sequential encoding as the phone: alpha ramps toward
// ~4h/day.

function YearView({ year, today, credit, loading, onPickDay }: {
  year: number
  today: string
  credit: Map<string, number>
  loading: boolean
  onPickDay: (d: string) => void
}) {
  const pad = (n: number) => String(n).padStart(2, '0')
  const heat = (mins: number): string => {
    const a = Math.min(0.9, 0.15 + (mins / 240) * 0.75)
    return `rgba(124,92,255,${a.toFixed(2)})`
  }
  return (
    <section className="rounded-lg border border-border bg-surface-secondary p-4">
      <h3 className="text-sm font-semibold text-text-primary mb-3">
        {year}
        {loading && (
          <span className="ml-2 text-xs font-normal text-text-muted animate-pulse">
            loading time log…
          </span>
        )}
      </h3>
      <div className="space-y-1.5">
        {Array.from({ length: 12 }, (_, mi) => {
          const daysIn = new Date(year, mi + 1, 0).getDate()
          const label = new Date(year, mi, 1, 12).toLocaleDateString(
            undefined, { month: 'short' })
          return (
            <div key={mi} className="flex items-center gap-2">
              <div className="w-8 text-[10px] text-text-muted shrink-0">{label}</div>
              <div className="flex-1 flex gap-[2px]">
                {Array.from({ length: daysIn }, (_, di) => {
                  const day = `${year}-${pad(mi + 1)}-${pad(di + 1)}`
                  const min = credit.get(day) || 0
                  return (
                    <button
                      key={di}
                      onClick={() => onPickDay(day)}
                      title={min > 0
                        ? `${day}: ${(min / 60).toFixed(1)}h logged`
                        : day}
                      className={`flex-1 h-4 rounded-[2px] cursor-pointer min-w-0 ${
                        day === today
                          ? 'outline outline-1 outline-accent'
                          : ''
                      } ${min > 0 ? '' : 'bg-white/[0.04] hover:bg-white/[0.08]'}`}
                      style={min > 0 ? { background: heat(min) } : undefined}
                    />
                  )
                })}
              </div>
            </div>
          )
        })}
      </div>
      <div className="text-[10px] text-text-muted mt-2">
        Every day of the year; brighter = more credited hours (your
        logged time, synced from the phone). Click a day to open it.
      </div>
    </section>
  )
}
