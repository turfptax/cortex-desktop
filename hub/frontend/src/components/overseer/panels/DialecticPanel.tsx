import { useState } from 'react'
import { fmtTime } from '../../../lib/time'
import {
  type DialecticRow,
  type DialecticListResp,
} from '../shared'

// ── Slice 3f.5 #3: Public Dialectic UI ──────────────────────

export function DialecticPanel({
  dialectics,
  counts,
  expandedId,
  setExpandedId,
  onResolve,
  onRefresh,
}: {
  dialectics: DialecticRow[]
  counts: DialecticListResp['counts'] | null
  expandedId: number | null
  setExpandedId: (id: number | null) => void
  onResolve: (id: number, resolution: 'opus' | 'gemma' | 'third' | 'productive', text?: string) => void
  onRefresh: () => void
}) {
  const open = dialectics.filter((d) => d.status === 'open')
  const productive = dialectics.filter((d) => d.status === 'productive')
  const resolved = dialectics.filter((d) => d.status === 'resolved')
  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-5xl mx-auto space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-base font-semibold text-text-primary">
              Open questions in the overseer's interpretation
            </h3>
            <p className="text-xs text-text-muted mt-1">
              Where Opus 4.7 and Gemma 3 generated different readings of
              the same source. The disagreement is the data — agree
              with one, propose a third, or mark as productive (don't
              resolve; stay live as a caveat).
            </p>
          </div>
          <button
            onClick={onRefresh}
            className="px-3 py-1.5 rounded-md text-xs font-medium bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-primary cursor-pointer"
          >
            Refresh
          </button>
        </div>
        {counts && (
          <div className="flex gap-2 flex-wrap text-xs">
            <span className="px-2 py-1 rounded-md bg-amber-500/15 text-amber-300">
              {counts.open} open
            </span>
            <span className="px-2 py-1 rounded-md bg-red-500/15 text-red-300">
              {counts.open_significant} significant
            </span>
            <span className="px-2 py-1 rounded-md bg-text-muted/15 text-text-muted">
              {counts.open_minor} minor
            </span>
            <span className="px-2 py-1 rounded-md bg-success/15 text-success">
              {counts.resolved} resolved
            </span>
            <span className="px-2 py-1 rounded-md bg-accent/15 text-accent-hover">
              {counts.productive} productive
            </span>
          </div>
        )}

        {open.length === 0 && resolved.length === 0 && productive.length === 0 ? (
          <div className="text-sm text-text-muted py-12 text-center">
            No paired generations yet. They land here automatically as
            the loop summarizes new sessions and imports.
          </div>
        ) : (
          <>
            <DialecticList
              title="Open"
              items={open}
              expandedId={expandedId}
              setExpandedId={setExpandedId}
              onResolve={onResolve}
              showResolve
              emptyHint="No open dialectics — all caught up."
            />
            {productive.length > 0 && (
              <DialecticList
                title="Productive (live caveats)"
                items={productive}
                expandedId={expandedId}
                setExpandedId={setExpandedId}
                onResolve={onResolve}
                showResolve={false}
              />
            )}
            {resolved.length > 0 && (
              <DialecticList
                title="Resolved"
                items={resolved}
                expandedId={expandedId}
                setExpandedId={setExpandedId}
                onResolve={onResolve}
                showResolve={false}
                collapsed
              />
            )}
          </>
        )}
      </div>
    </div>
  )
}

export function DialecticList({
  title,
  items,
  expandedId,
  setExpandedId,
  onResolve,
  showResolve,
  emptyHint,
  collapsed,
}: {
  title: string
  items: DialecticRow[]
  expandedId: number | null
  setExpandedId: (id: number | null) => void
  onResolve: (id: number, resolution: 'opus' | 'gemma' | 'third' | 'productive', text?: string) => void
  showResolve: boolean
  emptyHint?: string
  collapsed?: boolean
}) {
  const [show, setShow] = useState(!collapsed)
  return (
    <section>
      <button
        className="text-sm font-medium text-text-primary mb-3 flex items-center gap-2 cursor-pointer"
        onClick={() => setShow(!show)}
      >
        <span>{show ? '▾' : '▸'}</span>
        <span>{title} ({items.length})</span>
      </button>
      {show && (
        items.length === 0 ? (
          <div className="text-xs text-text-muted">{emptyHint}</div>
        ) : (
          <ul className="space-y-2">
            {items.map((d) => (
              <DialecticRowView
                key={d.id}
                d={d}
                expanded={expandedId === d.id}
                onToggle={() =>
                  setExpandedId(expandedId === d.id ? null : d.id)
                }
                onResolve={onResolve}
                showResolve={showResolve}
              />
            ))}
          </ul>
        )
      )}
    </section>
  )
}

export function DialecticRowView({
  d,
  expanded,
  onToggle,
  onResolve,
  showResolve,
}: {
  d: DialecticRow
  expanded: boolean
  onToggle: () => void
  onResolve: (id: number, resolution: 'opus' | 'gemma' | 'third' | 'productive', text?: string) => void
  showResolve: boolean
}) {
  const [thirdText, setThirdText] = useState('')
  const sevColor =
    d.severity === 'significant'
      ? 'bg-red-500/20 text-red-400'
      : d.severity === 'minor'
        ? 'bg-amber-500/20 text-amber-400'
        : 'bg-text-muted/20 text-text-muted'
  return (
    <li className="rounded-lg border border-border bg-surface-secondary">
      <button
        onClick={onToggle}
        className="w-full text-left p-3 flex items-start gap-3 cursor-pointer"
      >
        <span
          className={`px-1.5 py-0.5 rounded text-[10px] font-medium uppercase shrink-0 ${sevColor}`}
        >
          {d.severity}
        </span>
        <div className="flex-1 min-w-0">
          <div className="text-xs text-text-muted mb-0.5">
            {d.purpose} · {d.artifact_type}#{d.artifact_id} ·{' '}
            sim {(d.similarity * 100).toFixed(0)}% ·{' '}
            <span title={fmtTime((d as any).local_created_at, d.created_at)}>{fmtTime((d as any).local_created_at, d.created_at)}</span>
          </div>
          <div className="text-sm text-text-primary truncate">
            {d.diff_summary || `${d.opus_text.slice(0, 100)}…`}
          </div>
          {d.source_context && (
            <div className="text-[11px] text-text-muted mt-0.5 truncate">
              source: {d.source_context}
            </div>
          )}
        </div>
        <span className="text-text-muted text-lg leading-none shrink-0">
          {expanded ? '▾' : '▸'}
        </span>
      </button>
      {expanded && (
        <div className="border-t border-border p-3 space-y-3">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div className="rounded-md border border-border bg-surface-tertiary p-3">
              <div className="text-[10px] uppercase tracking-wide text-text-muted mb-1">
                Opus 4.7
              </div>
              <p className="text-sm text-text-primary leading-relaxed whitespace-pre-wrap">
                {d.opus_text}
              </p>
              <div className="text-[10px] text-text-muted mt-2">
                conf={d.opus_confidence} · ${d.opus_cost_usd.toFixed(4)}
              </div>
            </div>
            <div className="rounded-md border border-border bg-surface-tertiary p-3">
              <div className="text-[10px] uppercase tracking-wide text-text-muted mb-1">
                Gemma 3 27B
              </div>
              <p className="text-sm text-text-primary leading-relaxed whitespace-pre-wrap">
                {d.gemma_text}
              </p>
              <div className="text-[10px] text-text-muted mt-2">
                conf={d.gemma_confidence} · ${d.gemma_cost_usd.toFixed(4)}
              </div>
            </div>
          </div>
          {showResolve ? (
            <div className="space-y-2">
              <div className="flex flex-wrap gap-2">
                <button
                  onClick={() => onResolve(d.id, 'opus')}
                  className="px-3 py-1.5 rounded-md text-xs font-medium bg-accent/15 hover:bg-accent/25 text-accent-hover cursor-pointer"
                >
                  Agree with Opus
                </button>
                <button
                  onClick={() => onResolve(d.id, 'gemma')}
                  className="px-3 py-1.5 rounded-md text-xs font-medium bg-accent/15 hover:bg-accent/25 text-accent-hover cursor-pointer"
                >
                  Agree with Gemma
                </button>
                <button
                  onClick={() => onResolve(d.id, 'productive')}
                  className="px-3 py-1.5 rounded-md text-xs font-medium bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-primary cursor-pointer"
                  title="Don't resolve — keep as a live caveat in working memory"
                >
                  Mark productive (don't resolve)
                </button>
              </div>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={thirdText}
                  onChange={(e) => setThirdText(e.target.value)}
                  placeholder="Or propose a third reading…"
                  className="flex-1 rounded-md border border-border bg-surface-tertiary px-3 py-1.5 text-xs text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent"
                />
                <button
                  onClick={() => {
                    if (thirdText.trim()) {
                      onResolve(d.id, 'third', thirdText.trim())
                      setThirdText('')
                    }
                  }}
                  disabled={!thirdText.trim()}
                  className="px-3 py-1.5 rounded-md text-xs font-medium bg-accent hover:bg-accent-hover text-white cursor-pointer disabled:opacity-40"
                >
                  Submit third
                </button>
              </div>
            </div>
          ) : (
            <div className="text-xs text-text-muted">
              Status: {d.status}
              {d.resolution && <> · resolution: {d.resolution}</>}
              {d.resolution_text && (
                <div className="mt-1 italic text-text-secondary">
                  "{d.resolution_text}"
                </div>
              )}
              {d.resolved_at && (
                <div className="mt-1">
                  resolved at {d.resolved_at.slice(0, 19)}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </li>
  )
}

// JournalPanel + JournalEntryView were moved to JournalTab.tsx in
// Slice 5 CP3+CP4 (overseer reflections are now the bottom section
// of the Journal tab; human entries + temporal narratives sit above).
