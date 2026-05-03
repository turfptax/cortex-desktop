/**
 * Slice 5 CP3+CP4: Journal tab.
 *
 * The single home for anything reflective in the Hub. Three
 * stacked sections, top → bottom:
 *
 *   1. YOUR JOURNAL — free-form textarea + recent entries you've
 *      written. Multiple per day allowed (Tory's call: simpler data
 *      shape, no edit-state to manage). Auto-included in temporal
 *      narrative prompts when they fall in the period being
 *      summarized.
 *
 *   2. TEMPORAL NARRATIVES — Daily / Weekly / Monthly Sonnet
 *      rollups produced by the loop on a 22:00-local schedule.
 *      Latest of each kind shown; click "All <kind>" for history.
 *      Per-kind "Generate now" button bypasses the time gate.
 *
 *   3. OVERSEER REFLECTIONS — the original tick-based first-person
 *      journal. Append-only by the overseer; read-along for the
 *      user.
 *
 * Per Tory's locked principle: this is a quiet memory layer. No
 * notifications, no streaks, no nags. Read what you want to read,
 * write when you want to write.
 */

import { useCallback, useEffect, useState } from 'react'
import { apiFetch } from '../../lib/api'

// ── Types ────────────────────────────────────────────────────────

export interface OverseerJournalEntry {
  id: number
  written_at: string
  instance_id: string
  triggered_by: string
  body: string
  provisionality: 'high' | 'med' | 'low'
  model: string
  cost_usd: number
}

interface HumanJournalEntry {
  id: number
  text: string
  entry_type: string
  created_at: string
  local_created_at: string
}

interface TemporalNarrative {
  id: number
  kind: 'daily' | 'weekly' | 'monthly'
  period_start: string
  period_end: string
  period_label: string
  narrative: string
  cost_usd: number
  model: string
  triggered_by: string
  created_at: string
  local_created_at: string
}

interface ListHumanResp {
  ok: boolean
  entries?: HumanJournalEntry[]
  count?: number
  error?: string
}

interface ListTemporalResp {
  ok: boolean
  narratives?: TemporalNarrative[]
  count?: number
  error?: string
}

interface AddHumanResp {
  ok: boolean
  id?: number
  error?: string
}

interface GenerateTemporalResp {
  ok: boolean
  kind?: string
  period_label?: string
  narrative?: string
  cost_usd?: number
  model?: string
  latency_ms?: number
  error?: string
  skipped?: boolean
  reason?: string
}

// ── Helpers ──────────────────────────────────────────────────────

function fmtRelativeTime(iso: string): string {
  if (!iso) return ''
  const d = new Date(iso.includes('T') ? iso : iso.replace(' ', 'T') + 'Z')
  const ms = Date.now() - d.getTime()
  const mins = Math.floor(ms / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(hrs / 24)
  if (days < 7) return `${days}d ago`
  if (days < 30) return `${Math.round(days / 7)}w ago`
  if (days < 365) return `${Math.round(days / 30)}mo ago`
  return `${Math.round(days / 365)}y ago`
}

function fmtLocalShort(iso: string): string {
  if (!iso) return ''
  // local_created_at is ISO with offset like "2026-05-03T11:30:00-05:00"
  // — just take the first 16 chars for display.
  return iso.slice(0, 16).replace('T', ' ')
}

// ── Section 1: Human journal ─────────────────────────────────────

function HumanJournalSection() {
  const [entries, setEntries] = useState<HumanJournalEntry[]>([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showAll, setShowAll] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const r = await apiFetch<ListHumanResp>('/overseer/human-journal?limit=200')
      setEntries(r.entries || [])
    } catch (e: any) {
      setError(e?.message || String(e))
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const handleSave = async () => {
    const trimmed = text.trim()
    if (!trimmed) return
    setBusy(true)
    setError(null)
    try {
      const r = await apiFetch<AddHumanResp>(
        '/overseer/human-journal',
        { method: 'POST', body: JSON.stringify({ text: trimmed, entry_type: 'free' }) },
      )
      if (!r.ok) {
        setError(r.error || 'save failed')
        return
      }
      setText('')
      void refresh()
    } catch (e: any) {
      setError(e?.message || String(e))
    } finally {
      setBusy(false)
    }
  }

  const handleDelete = async (id: number) => {
    if (!confirm('Delete this entry?')) return
    try {
      await apiFetch('/overseer/human-journal/delete', {
        method: 'POST', body: JSON.stringify({ id }),
      })
      void refresh()
    } catch (e: any) {
      setError(e?.message || String(e))
    }
  }

  const visibleEntries = showAll ? entries : entries.slice(0, 5)

  return (
    <section className="space-y-3">
      <header>
        <h3 className="text-base font-semibold text-text-primary">
          Your journal
        </h3>
        <p className="text-xs text-text-muted mt-1">
          Free-form notes — what you're thinking, what you're noticing.
          Auto-included in the daily/weekly/monthly narratives below
          when they fall in the period being summarized. Multiple per
          day is fine.
        </p>
      </header>

      <div className="space-y-2">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
              e.preventDefault()
              void handleSave()
            }
          }}
          placeholder="What's on your mind? (Cmd/Ctrl+Enter to save)"
          rows={3}
          className="w-full px-3 py-2 bg-surface-tertiary text-text-primary text-sm rounded-md border border-border focus:outline-none focus:border-accent resize-y placeholder:text-text-muted/60"
        />
        <div className="flex items-center gap-2">
          <button
            onClick={handleSave}
            disabled={busy || !text.trim()}
            className="px-3 py-1.5 rounded-md text-xs font-medium bg-accent text-white hover:bg-accent-hover cursor-pointer disabled:opacity-40"
          >
            {busy ? 'Saving…' : 'Save entry'}
          </button>
          {error && (
            <span className="text-xs text-red-400">{error}</span>
          )}
          <span className="ml-auto text-[11px] text-text-muted">
            {entries.length} entr{entries.length === 1 ? 'y' : 'ies'} total
          </span>
        </div>
      </div>

      {entries.length > 0 && (
        <div className="space-y-2">
          <ul className="space-y-2">
            {visibleEntries.map((e) => (
              <li
                key={e.id}
                className="rounded-lg border border-border bg-surface-secondary/40 px-3 py-2 group"
              >
                <div className="flex items-baseline gap-2 mb-1">
                  <span className="text-[11px] text-text-muted">
                    {fmtLocalShort(e.local_created_at || e.created_at)}
                  </span>
                  <span className="text-[10px] text-text-muted/60 ml-auto">
                    {fmtRelativeTime(e.created_at)}
                  </span>
                  <button
                    onClick={() => handleDelete(e.id)}
                    className="text-[11px] text-text-muted/40 hover:text-red-400 cursor-pointer opacity-0 group-hover:opacity-100 transition-opacity"
                    title="Delete entry"
                  >
                    ✕
                  </button>
                </div>
                <p className="text-sm text-text-secondary leading-relaxed whitespace-pre-wrap">
                  {e.text}
                </p>
              </li>
            ))}
          </ul>
          {entries.length > 5 && (
            <button
              onClick={() => setShowAll((v) => !v)}
              className="text-[11px] uppercase tracking-wider text-text-muted hover:text-text-primary cursor-pointer"
            >
              {showAll ? 'Show recent' : `Show all ${entries.length} entries`}
            </button>
          )}
        </div>
      )}
    </section>
  )
}

// ── Section 2: Temporal narratives ───────────────────────────────

const KIND_LABEL: Record<TemporalNarrative['kind'], string> = {
  daily: 'Daily',
  weekly: 'Weekly',
  monthly: 'Monthly',
}

const KIND_ORDER: TemporalNarrative['kind'][] = ['daily', 'weekly', 'monthly']

function TemporalNarrativesSection() {
  const [narratives, setNarratives] = useState<TemporalNarrative[]>([])
  const [loading, setLoading] = useState(false)
  const [busyKind, setBusyKind] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set())
  const [showAllKind, setShowAllKind] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const r = await apiFetch<ListTemporalResp>('/overseer/temporal?limit=200')
      setNarratives(r.narratives || [])
    } catch (e: any) {
      setError(e?.message || String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const handleGenerate = async (kind: string) => {
    setBusyKind(kind)
    setError(null)
    try {
      const r = await apiFetch<GenerateTemporalResp>(
        '/overseer/temporal/generate',
        { method: 'POST', body: JSON.stringify({ kind, force: true }) },
      )
      if (!r.ok) {
        setError(r.error || 'generate failed')
        return
      }
      void refresh()
    } catch (e: any) {
      setError(e?.message || String(e))
    } finally {
      setBusyKind(null)
    }
  }

  const toggleExpanded = useCallback((id: number) => {
    setExpandedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const byKind = (kind: TemporalNarrative['kind']) =>
    narratives.filter((n) => n.kind === kind)

  return (
    <section className="space-y-3">
      <header>
        <h3 className="text-base font-semibold text-text-primary">
          Temporal narratives
        </h3>
        <p className="text-xs text-text-muted mt-1">
          Daily / Weekly / Monthly Sonnet rollups. Loop fires daily at 22:00 local,
          weekly Sunday 22:00, monthly the 1st 22:00 (skipped if no daily in past
          14 days). Click "Generate now" to bypass the schedule.
        </p>
        {error && <p className="text-xs text-red-400 mt-1">{error}</p>}
      </header>

      <div className="space-y-4">
        {KIND_ORDER.map((kind) => {
          const rows = byKind(kind)
          const showAll = showAllKind === kind
          const visible = showAll ? rows : rows.slice(0, 1)
          const isBusy = busyKind === kind
          return (
            <div key={kind} className="rounded-lg border border-border bg-surface-secondary/40 overflow-hidden">
              <div className="px-4 py-2.5 flex items-baseline gap-3 border-b border-border bg-surface-tertiary/30">
                <h4 className="text-sm font-semibold text-text-primary">
                  {KIND_LABEL[kind]}
                </h4>
                <span className="text-[11px] text-text-muted">
                  {rows.length} entr{rows.length === 1 ? 'y' : 'ies'}
                </span>
                {rows.length > 1 && (
                  <button
                    onClick={() => setShowAllKind(showAll ? null : kind)}
                    className="text-[11px] uppercase tracking-wider text-text-muted hover:text-text-primary cursor-pointer"
                  >
                    {showAll ? 'Latest only' : `All ${kind}`}
                  </button>
                )}
                <button
                  onClick={() => handleGenerate(kind)}
                  disabled={isBusy || loading}
                  className="ml-auto text-[11px] uppercase tracking-wider text-accent-hover hover:text-accent cursor-pointer disabled:opacity-40"
                >
                  {isBusy ? 'Generating…' : 'Generate now'}
                </button>
              </div>
              {visible.length === 0 ? (
                <div className="px-4 py-6 text-xs text-text-muted text-center italic">
                  {loading
                    ? 'Loading…'
                    : `No ${kind} narrative yet. Click "Generate now" to create one or wait for the loop's 22:00 local trigger.`}
                </div>
              ) : (
                <ul className="divide-y divide-border">
                  {visible.map((n) => (
                    <TemporalEntry
                      key={n.id}
                      n={n}
                      expanded={expandedIds.has(n.id)}
                      toggleExpanded={() => toggleExpanded(n.id)}
                    />
                  ))}
                </ul>
              )}
            </div>
          )
        })}
      </div>
    </section>
  )
}

function TemporalEntry({
  n,
  expanded,
  toggleExpanded,
}: {
  n: TemporalNarrative
  expanded: boolean
  toggleExpanded: () => void
}) {
  // Truncate to first paragraph by default — same pattern as
  // ProjectsTab's narrative block.
  const paragraphs = n.narrative.split(/\n\n+/).map((p) => p.trim()).filter(Boolean)
  const hasMore = paragraphs.length > 1
  const visibleBody = expanded ? n.narrative : (paragraphs[0] || n.narrative)
  return (
    <li className="px-4 py-3">
      <div className="flex items-baseline gap-3 mb-2">
        <span className="text-sm font-mono text-text-primary tabular-nums">
          {n.period_label}
        </span>
        <span className="text-[11px] text-text-muted">
          {fmtRelativeTime(n.created_at)}
          {n.triggered_by === 'manual' && (
            <span className="ml-1 text-text-muted/60">· manual</span>
          )}
        </span>
        <span className="ml-auto text-[10px] text-text-muted/60 tabular-nums">
          ${n.cost_usd?.toFixed(4) || '0'}
        </span>
      </div>
      <p className="text-sm text-text-secondary leading-relaxed whitespace-pre-wrap">
        {visibleBody}
      </p>
      {hasMore && (
        <button
          onClick={toggleExpanded}
          className="mt-1 text-[11px] uppercase tracking-wider text-accent-hover hover:text-accent cursor-pointer"
        >
          {expanded ? 'Show less ▴' : 'Read full ▾'}
        </button>
      )}
    </li>
  )
}

// ── Section 3: Overseer reflections (existing) ──────────────────

function OverseerReflectionsSection({
  entries,
  onRefresh,
}: {
  entries: OverseerJournalEntry[]
  onRefresh: () => void
}) {
  const reversed = [...entries].reverse() // newest first
  return (
    <section className="space-y-3">
      <header className="flex items-baseline gap-3">
        <div>
          <h3 className="text-base font-semibold text-text-primary">
            Overseer reflections
          </h3>
          <p className="text-xs text-text-muted mt-1">
            The overseer's first-person notes at the end of notable
            ticks. Append-only — these are for future overseer instances
            to read at boot. You read along.
          </p>
        </div>
        <button
          onClick={onRefresh}
          className="ml-auto px-3 py-1.5 rounded-md text-xs font-medium bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-primary cursor-pointer"
        >
          Refresh
        </button>
      </header>
      {reversed.length === 0 ? (
        <div className="text-sm text-text-muted py-6 text-center italic">
          No reflections yet. They appear after the loop runs ticks
          with notable work.
        </div>
      ) : (
        <ul className="space-y-3">
          {reversed.map((j) => (
            <OverseerJournalEntryView key={j.id} j={j} />
          ))}
        </ul>
      )}
    </section>
  )
}

function OverseerJournalEntryView({ j }: { j: OverseerJournalEntry }) {
  const provColor =
    j.provisionality === 'high'
      ? 'bg-success/15 text-success'
      : j.provisionality === 'low'
        ? 'bg-red-500/15 text-red-400'
        : 'bg-text-muted/15 text-text-muted'
  return (
    <li className="rounded-lg border border-border bg-surface-secondary p-4">
      <div className="flex items-center gap-2 mb-2">
        <span
          className={`px-1.5 py-0.5 rounded text-[10px] font-medium uppercase ${provColor}`}
          title="Overseer's self-reported confidence in this entry"
        >
          prov: {j.provisionality}
        </span>
        <span className="text-xs text-text-muted">
          {j.written_at?.slice(0, 19)}
        </span>
        <span className="text-[11px] text-text-muted ml-auto truncate max-w-xs font-mono">
          {j.triggered_by} · {j.model}
        </span>
      </div>
      <p className="text-sm text-text-primary leading-relaxed whitespace-pre-wrap">
        {j.body}
      </p>
    </li>
  )
}

// ── Top-level export ─────────────────────────────────────────────

export function JournalTab({
  overseerEntries,
  onRefreshOverseerJournal,
}: {
  overseerEntries: OverseerJournalEntry[]
  onRefreshOverseerJournal: () => void
}) {
  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-3xl mx-auto space-y-8">
        <HumanJournalSection />
        <TemporalNarrativesSection />
        <OverseerReflectionsSection
          entries={overseerEntries}
          onRefresh={onRefreshOverseerJournal}
        />
      </div>
    </div>
  )
}
