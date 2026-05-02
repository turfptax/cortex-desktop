import { useEffect, useState } from 'react'
import { apiFetch } from '../../lib/api'

// ── Types ─────────────────────────────────────────────────────

interface StatusResp {
  ok: boolean
  plugin?: string
  version?: string
  overseer_db?: Record<string, number>
  core_memory_open?: boolean
  core_db_path?: string
  core_stats?: Record<string, any>
  loop_running?: boolean
  last_tick_at?: string | null
  working_memory_built_at?: string | null
  llm_default_backend?: string
  seed_summary?: any
  error?: string
}

interface WMQuestionEvidence {
  contribution: string
  reason: string
  evidence_table: string
  evidence_id: number
  evidence_body?: string
  evidence_label?: string
  evidence_confidence?: string
  evidence_created_at?: string
}

interface WMTopQuestion {
  id: number
  question: string
  body?: string
  confidence: string
  lifecycle: string
  evidence_count: number
  tags?: string[]
  recent_evidence?: WMQuestionEvidence[]
}

interface WMUnfiledGist {
  id: number
  body: string
  period_label?: string
  created_at?: string
}

interface WorkingMemory {
  built_at?: string
  schema_version?: number
  top_questions?: WMTopQuestion[]   // Slice 3f.5 #2: PRIMARY view
  top_projects?: Array<{
    tag: string
    name: string
    last_touched: string
    description?: string
  }>
  recent_decisions?: Array<{ id: number; content: string; created_at: string }>
  open_todos?: Array<{ id: number; content: string; created_at: string }>
  open_questions?: Array<{   // legacy back-compat
    id: number
    question: string
    confidence: string
    tags?: string[]
  }>
  recent_themes?: Array<{ id: number; title: string; confidence: string }>
  recent_episode_titles?: string[]
  last_week_digest?: string
  unfiled_recent_gists?: WMUnfiledGist[]
  future_overseer_notes_count?: number
  journal_entry_count?: number
  blindspots?: BlindspotRow[]
}

interface WorkingMemoryResp {
  ok: boolean
  working_memory?: WorkingMemory | null
  source?: string
  working_memory_status?: string
  hint?: string
}

interface ImportRow {
  id: string
  source: string
  source_path: string
  project: string
  cwd: string
  git_branch: string
  started_at: string | null
  ended_at: string | null
  duration_minutes: number
  message_count: number
  user_message_count: number
  assistant_message_count: number
  tool_use_count: number
  bytes_size: number
  file_hash: string
  imported_at: string
  processed: boolean
}

interface ImportsResp {
  ok: boolean
  imports?: ImportRow[]
  total?: number
}

interface ScanRow {
  path: string
  session_id: string
  project_folder: string
  size_bytes: number
  mtime: number
  mtime_iso: string
}

interface ScanResp {
  ok: boolean
  found?: ScanRow[]
  total?: number
  scanned_dir?: string
  note?: string
}

interface LoopResp {
  ok: boolean
  started_at?: string
  ticks_run?: number
  ticks_failed?: number
  last_tick_at?: string | null
  last_tick_summary?: any
  last_error?: string
  running?: boolean
}

interface LlmStatsResp {
  ok: boolean
  stats?: Array<{
    actual_backend: string
    calls: number
    oks: number
    degraded_calls: number
    avg_ms: number
    total_cost_usd: number
  }>
  period_days?: number
}

interface ChatMessage {
  id: number
  role: 'user' | 'assistant' | 'system'
  content: string
  created_at: string
  backend?: string
  model?: string
  latency_ms?: number
  cost_usd?: number
}

interface ChatHistoryResp {
  ok: boolean
  messages?: ChatMessage[]
  total?: number
}

interface ChatSendResp {
  ok: boolean
  reply?: string
  model?: string
  backend?: string
  latency_ms?: number
  cost_usd?: number
  error?: string
}

interface NotificationRow {
  id: number
  severity: 'info' | 'warn' | 'important'
  title: string
  body: string
  rule_name: string
  rule_key: string
  related_table: string
  related_id: string
  created_at: string
  dismissed_at: string | null
}

interface NotificationsResp {
  ok: boolean
  notifications?: NotificationRow[]
  unread_count?: number
}

interface BudgetSnapshot {
  date: string
  cost_used_usd: number
  cost_max_usd: number
  cost_remaining_usd: number
  calls_used: number
  calls_max: number
  calls_remaining: number
  exhausted: boolean
}

interface BudgetResp {
  ok: boolean
  budget?: BudgetSnapshot
}

// ── Slice 3f + 3f.5 types ─────────────────────────────────────

interface DialecticRow {
  id: number
  artifact_type: string
  artifact_id: number | null
  purpose: string
  opus_model: string
  gemma_model: string
  opus_text: string
  gemma_text: string
  opus_confidence: string
  gemma_confidence: string
  severity: 'none' | 'minor' | 'significant'
  similarity: number
  diff_summary: string
  source_context: string
  status: 'open' | 'resolved' | 'productive'
  resolution: string
  resolution_text: string
  resolved_at: string | null
  opus_cost_usd: number
  gemma_cost_usd: number
  created_at: string
}

interface DialecticListResp {
  ok: boolean
  dialectics?: DialecticRow[]
  counts?: {
    open: number
    open_significant: number
    open_minor: number
    resolved: number
    productive: number
    total: number
  }
}

interface JournalEntry {
  id: number
  written_at: string
  instance_id: string
  triggered_by: string
  body: string
  provisionality: 'high' | 'med' | 'low'
  model: string
  cost_usd: number
}

interface JournalResp {
  ok: boolean
  entries?: JournalEntry[]
  total?: number
}

interface BlindspotRow {
  id: number
  model_pattern: string
  topic_pattern: string
  direction: string
  confidence_adjustment: number
  body: string
  rationale: string
  confidence: string
  is_active: number
  apply_count: number
  last_applied_at: string | null
}

// ── Helpers ───────────────────────────────────────────────────

function fmtBytes(n?: number): string {
  if (!n || n < 0) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

function fmtDuration(min?: number): string {
  if (!min || min <= 0) return '—'
  if (min < 60) return `${min}m`
  if (min < 60 * 24) return `${(min / 60).toFixed(1)}h`
  return `${(min / 60 / 24).toFixed(1)}d`
}

function fmtRelative(iso?: string | null): string {
  if (!iso) return '—'
  const t = new Date(iso).getTime()
  const ms = Date.now() - t
  if (Number.isNaN(ms) || ms < 0) return iso.slice(0, 19)
  const sec = Math.round(ms / 1000)
  if (sec < 60) return `${sec}s ago`
  if (sec < 3600) return `${Math.round(sec / 60)}m ago`
  if (sec < 86400) return `${Math.round(sec / 3600)}h ago`
  return `${Math.round(sec / 86400)}d ago`
}

// ── Page ──────────────────────────────────────────────────────

type Tab = 'overview' | 'chat' | 'dialectic' | 'journal' | 'notifications'

export function OverseerPage() {
  const [tab, setTab] = useState<Tab>('overview')
  const [status, setStatus] = useState<StatusResp | null>(null)
  const [wm, setWm] = useState<WorkingMemoryResp | null>(null)
  const [imports, setImports] = useState<ImportRow[]>([])
  const [loop, setLoop] = useState<LoopResp | null>(null)
  const [llmStats, setLlmStats] = useState<LlmStatsResp | null>(null)
  const [scan, setScan] = useState<ScanResp | null>(null)
  const [selectedPaths, setSelectedPaths] = useState<Set<string>>(new Set())
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([])
  const [chatInput, setChatInput] = useState<string>('')
  const [chatSending, setChatSending] = useState<boolean>(false)
  const [notifications, setNotifications] = useState<NotificationRow[]>([])
  const [notificationsUnread, setNotificationsUnread] = useState<number>(0)
  const [budget, setBudget] = useState<BudgetSnapshot | null>(null)
  const [dialectics, setDialectics] = useState<DialecticRow[]>([])
  const [dialecticCounts, setDialecticCounts] = useState<DialecticListResp['counts'] | null>(null)
  const [journal, setJournal] = useState<JournalEntry[]>([])
  const [blindspots, setBlindspots] = useState<BlindspotRow[]>([])
  const [expandedDialecticId, setExpandedDialecticId] = useState<number | null>(null)
  const [busy, setBusy] = useState<string>('')
  const [lastAction, setLastAction] = useState<string>('')
  const [error, setError] = useState<string>('')

  const refreshAll = async () => {
    setError('')
    try {
      const [s, w, l, ls, im, n, b, d, bs] = await Promise.all([
        apiFetch<StatusResp>('/overseer/status'),
        apiFetch<WorkingMemoryResp>('/overseer/working-memory'),
        apiFetch<LoopResp>('/overseer/loop'),
        apiFetch<LlmStatsResp>('/overseer/llm/stats?days=7'),
        apiFetch<ImportsResp>('/overseer/imports?limit=200'),
        apiFetch<NotificationsResp>('/overseer/notifications'),
        apiFetch<BudgetResp>('/overseer/budget'),
        apiFetch<DialecticListResp>('/overseer/dialectic?limit=100'),
        apiFetch<{ ok: boolean; blindspots?: BlindspotRow[] }>(
          '/overseer/blindspots?active_only=1'
        ),
      ])
      setStatus(s)
      setWm(w)
      setLoop(l)
      setLlmStats(ls)
      setImports(im.imports || [])
      setNotifications(n.notifications || [])
      setNotificationsUnread(n.unread_count || 0)
      setBudget(b.budget || null)
      setDialectics(d.dialectics || [])
      setDialecticCounts(d.counts || null)
      setBlindspots(bs.blindspots || [])
    } catch (e: any) {
      setError(`Refresh failed: ${e?.message || e}`)
    }
  }

  const refreshJournal = async () => {
    try {
      const r = await apiFetch<JournalResp>('/overseer/journal?limit=100')
      setJournal(r.entries || [])
    } catch (e: any) {
      setError(`Journal refresh failed: ${e?.message || e}`)
    }
  }

  const refreshChat = async () => {
    try {
      const r = await apiFetch<ChatHistoryResp>('/overseer/chat/history?limit=200')
      setChatMessages(r.messages || [])
    } catch (e: any) {
      setError(`Chat refresh failed: ${e?.message || e}`)
    }
  }

  useEffect(() => {
    refreshAll()
    const t = setInterval(refreshAll, 30000)
    return () => clearInterval(t)
  }, [])

  useEffect(() => {
    if (tab === 'chat') refreshChat()
    if (tab === 'journal') refreshJournal()
  }, [tab])

  const handleSendChat = async () => {
    const message = chatInput.trim()
    if (!message || chatSending) return
    setChatSending(true)
    setError('')
    // Optimistic: append user msg locally so the input clears immediately
    const optimistic: ChatMessage = {
      id: -Date.now(),
      role: 'user',
      content: message,
      created_at: new Date().toISOString(),
    }
    setChatMessages((prev) => [...prev, optimistic])
    setChatInput('')
    try {
      const r = await apiFetch<ChatSendResp>('/overseer/chat', {
        method: 'POST',
        body: JSON.stringify({ message }),
      })
      if (!r.ok) {
        setError(`Chat error: ${r.error || 'unknown'}`)
      }
      // Always re-fetch so we get the persisted IDs + assistant reply
      await refreshChat()
    } catch (e: any) {
      setError(`Chat failed: ${e?.message || e}`)
    } finally {
      setChatSending(false)
    }
  }

  const handleClearChat = async () => {
    if (!confirm('Clear the entire chat thread? This cannot be undone.')) return
    try {
      await apiFetch<any>('/overseer/chat/clear', { method: 'POST' })
      setChatMessages([])
      setLastAction('Chat thread cleared')
    } catch (e: any) {
      setError(`Clear failed: ${e?.message || e}`)
    }
  }

  const handleDismissNotification = async (id: number) => {
    try {
      await apiFetch<any>('/overseer/notifications/dismiss', {
        method: 'POST',
        body: JSON.stringify({ id }),
      })
      await refreshAll()
    } catch (e: any) {
      setError(`Dismiss failed: ${e?.message || e}`)
    }
  }

  const handleDismissAllNotifications = async () => {
    try {
      await apiFetch<any>('/overseer/notifications/dismiss', {
        method: 'POST',
        body: JSON.stringify({ all: true }),
      })
      await refreshAll()
    } catch (e: any) {
      setError(`Dismiss-all failed: ${e?.message || e}`)
    }
  }

  const handleResolveDialectic = async (
    id: number,
    resolution: 'opus' | 'gemma' | 'third' | 'productive',
    resolution_text = ''
  ) => {
    try {
      await apiFetch<any>('/overseer/dialectic/resolve', {
        method: 'POST',
        body: JSON.stringify({ id, resolution, resolution_text }),
      })
      setExpandedDialecticId(null)
      await refreshAll()
    } catch (e: any) {
      setError(`Resolve failed: ${e?.message || e}`)
    }
  }

  const handleOpenNotificationInChat = (n: NotificationRow) => {
    // Pre-fill a chat prompt from the notification's content and switch
    // to the chat tab. The user can edit before sending. This is the
    // minimal feedback loop — full per-rule actions (Archive/Snooze/etc.)
    // come in 3f.
    const ruleHints: Record<string, string> = {
      stale_active_project:
        "Should I archive this, ramp it back up, or is the staleness fine?",
      automation_anomaly:
        "What likely went wrong, and is it worth investigating?",
      import_backlog:
        "Should I just kick off /backfill, or is there a better strategy?",
    }
    const hint = ruleHints[n.rule_name] ||
      "What do you make of this, and what should I do?"
    const prefill = [
      `You flagged: "${n.title}"`,
      n.body ? `\nDetails: ${n.body}` : "",
      `\nRule: ${n.rule_name}, key: ${n.rule_key}`,
      `\n\n${hint}`,
    ].join("")
    setChatInput(prefill)
    setTab('chat')
  }

  const handleScan = async () => {
    setBusy('Scanning ~/.claude/projects/...')
    setLastAction('')
    setError('')
    try {
      const r = await apiFetch<ScanResp>('/overseer/scan/claude-code')
      setScan(r)
      setLastAction(`Scan found ${r.total ?? 0} Claude Code session files`)
      // Default-select files NOT already imported
      // ImportRow.id is "claude-code:<uuid>" — strip the prefix to compare
      // against ScanRow.session_id (the bare uuid).
      const known = new Set(
        imports.map((i) => i.id.split(':').slice(1).join(':') || i.id)
      )
      const sel = new Set<string>()
      for (const f of r.found || []) {
        if (!known.has(f.session_id)) sel.add(f.path)
      }
      setSelectedPaths(sel)
    } catch (e: any) {
      setError(`Scan failed: ${e?.message || e}`)
    } finally {
      setBusy('')
    }
  }

  const handleImportSelected = async () => {
    if (selectedPaths.size === 0) return
    const paths = Array.from(selectedPaths)
    setBusy(`Uploading ${paths.length} file(s) to Pi…`)
    setLastAction('')
    setError('')
    try {
      const r = await apiFetch<any>('/overseer/import', {
        method: 'POST',
        body: JSON.stringify({
          paths,
          source: 'claude-code',
          skip_already_imported: true,
        }),
      })
      const c = r?.counts || {}
      setLastAction(
        `Import: ${c.imported ?? 0} new, ${c.skipped ?? 0} already on Pi (dedup), ${c.failed ?? 0} failed (of ${c.requested ?? 0})`
      )
      setSelectedPaths(new Set())
      await refreshAll()
    } catch (e: any) {
      setError(`Import failed: ${e?.message || e}`)
    } finally {
      setBusy('')
    }
  }

  const handleTickNow = async () => {
    setBusy('Running overseer tick…')
    setLastAction('')
    setError('')
    try {
      const r = await apiFetch<any>('/overseer/tick-now', {
        method: 'POST',
        body: JSON.stringify({}),
      })
      const s = r?.summary || {}
      setLastAction(
        `Tick: sessions=${s.sessions_summarized ?? 0}, imports=${s.imports_summarized ?? 0}, notes=${s.notes_tagged ?? 0}, calls=${s.budget?.calls_used ?? 0}, cost=$${s.budget?.cost_used_usd ?? 0}`
      )
      await refreshAll()
    } catch (e: any) {
      setError(`Tick failed: ${e?.message || e}`)
    } finally {
      setBusy('')
    }
  }

  const handleDeleteImport = async (id: string) => {
    if (!confirm(`Delete import ${id}? The .jsonl on Pi will be removed.`)) return
    setBusy(`Deleting ${id}…`)
    setError('')
    try {
      await apiFetch<any>('/overseer/imports/delete', {
        method: 'POST',
        body: JSON.stringify({ id, remove_file: true }),
      })
      await refreshAll()
    } catch (e: any) {
      setError(`Delete failed: ${e?.message || e}`)
    } finally {
      setBusy('')
    }
  }

  const toggleSelect = (path: string) => {
    setSelectedPaths((prev) => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-6 py-3 border-b border-border bg-surface-secondary">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div className="flex items-center gap-3 flex-wrap">
            <h2 className="text-base font-semibold text-text-primary">
              Overseer
            </h2>
            {status?.loop_running ? (
              <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-success/20 text-success">
                Loop running
              </span>
            ) : (
              <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-text-muted/20 text-text-muted">
                Loop idle
              </span>
            )}
            {budget && (
              <BudgetIndicator budget={budget} />
            )}
            {status?.working_memory_built_at && (
              <span className="text-xs text-text-muted">
                wm built {fmtRelative(status.working_memory_built_at)}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            {busy && (
              <span className="text-xs text-text-muted">{busy}</span>
            )}
            <div className="flex gap-1 bg-surface-tertiary rounded-lg p-0.5">
              {([
                ['overview', 'Overview'],
                ['chat', 'Chat'],
                ['dialectic', `Dialectic${dialecticCounts && dialecticCounts.open > 0 ? ` (${dialecticCounts.open})` : ''}`],
                ['journal', 'Journal'],
                ['notifications', `Bell${notificationsUnread > 0 ? ` (${notificationsUnread})` : ''}`],
              ] as const).map(([id, label]) => (
                <button
                  key={id}
                  onClick={() => setTab(id as Tab)}
                  className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors cursor-pointer ${
                    tab === id
                      ? 'bg-accent text-white'
                      : 'text-text-secondary hover:text-text-primary'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
            <button
              onClick={refreshAll}
              disabled={!!busy}
              className="px-3 py-1.5 rounded-md text-xs font-medium bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-primary cursor-pointer disabled:opacity-50"
            >
              Refresh
            </button>
            <button
              onClick={handleTickNow}
              disabled={!!busy}
              className="px-3 py-1.5 rounded-md text-xs font-medium bg-accent hover:bg-accent-hover text-white cursor-pointer disabled:opacity-50"
            >
              Tick Now
            </button>
          </div>
        </div>
        {lastAction && (
          <div className="mt-2 text-xs text-success">{lastAction}</div>
        )}
        {error && (
          <div className="mt-2 text-xs text-red-400">{error}</div>
        )}
      </div>

      {/* Body */}
      {tab === 'chat' && (
        <ChatPanel
          messages={chatMessages}
          input={chatInput}
          setInput={setChatInput}
          sending={chatSending}
          onSend={handleSendChat}
          onClear={handleClearChat}
          onRefresh={refreshChat}
        />
      )}
      {tab === 'notifications' && (
        <NotificationsPanel
          notifications={notifications}
          onDismiss={handleDismissNotification}
          onDismissAll={handleDismissAllNotifications}
          onOpenInChat={handleOpenNotificationInChat}
        />
      )}
      {tab === 'dialectic' && (
        <DialecticPanel
          dialectics={dialectics}
          counts={dialecticCounts}
          expandedId={expandedDialecticId}
          setExpandedId={setExpandedDialecticId}
          onResolve={handleResolveDialectic}
          onRefresh={refreshAll}
        />
      )}
      {tab === 'journal' && (
        <JournalPanel
          entries={journal}
          onRefresh={refreshJournal}
        />
      )}
      {tab === 'overview' && (
      <div className="flex-1 overflow-y-auto p-6 space-y-6">

        {/* Stats grid */}
        <section className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard
            label="Gists"
            value={status?.overseer_db?.summaries_gist ?? 0}
            sub={`${status?.overseer_db?.processed_sessions ?? 0} sessions, ${status?.overseer_db?.processed_imported_sessions ?? 0} imports`}
          />
          <StatCard
            label="Themes"
            value={status?.overseer_db?.summaries_theme ?? 0}
            sub={`${status?.overseer_db?.summaries_episode ?? 0} episodes`}
          />
          <StatCard
            label="Open Questions"
            value={status?.overseer_db?.open_questions ?? 0}
            sub={`${status?.overseer_db?.patterns ?? 0} patterns, ${status?.overseer_db?.drift_observations ?? 0} drift`}
          />
          <StatCard
            label="LLM Calls"
            value={status?.overseer_db?.llm_calls ?? 0}
            sub={
              llmStats?.stats?.[0]
                ? `$${(llmStats.stats[0].total_cost_usd ?? 0).toFixed(4)} last ${llmStats.period_days}d`
                : '—'
            }
          />
        </section>

        {/* Slice 3f.5 #4: blindspots indicator */}
        {blindspots.length > 0 && (
          <Card title={`Known blindspots (${blindspots.length} active — apply to interpretations below)`}>
            <ul className="space-y-2 text-xs">
              {blindspots.slice(0, 8).map((b) => (
                <li key={b.id} className="flex gap-2">
                  <span className="text-text-muted font-mono shrink-0">
                    {b.model_pattern}
                  </span>
                  <span className="text-text-secondary">{b.body}</span>
                  {b.confidence_adjustment !== 0 && (
                    <span className={`shrink-0 text-[10px] uppercase font-medium ${
                      b.confidence_adjustment > 0 ? 'text-amber-400' : 'text-text-muted'
                    }`}>
                      {b.confidence_adjustment > 0 ? '+1 conf' : '-1 conf'}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </Card>
        )}

        {/* Working memory */}
        <Card title="Working Memory">
          {!wm?.working_memory ? (
            <div className="text-sm text-text-muted">
              {wm?.hint || 'No working memory yet.'}
            </div>
          ) : (
            <WorkingMemoryView wm={wm.working_memory} />
          )}
        </Card>

        {/* Imports */}
        <Card title="Imported Claude Sessions">
          <div className="flex items-center gap-2 mb-3">
            <button
              onClick={handleScan}
              disabled={!!busy}
              className="px-3 py-1.5 rounded-md text-xs font-medium bg-accent hover:bg-accent-hover text-white cursor-pointer disabled:opacity-50"
            >
              Scan ~/.claude/projects/
            </button>
            <button
              onClick={handleImportSelected}
              disabled={!!busy || selectedPaths.size === 0}
              className="px-3 py-1.5 rounded-md text-xs font-medium bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-primary cursor-pointer disabled:opacity-50"
            >
              Import {selectedPaths.size > 0 ? `(${selectedPaths.size})` : ''}
            </button>
            <span className="text-xs text-text-muted ml-auto">
              {imports.length} imported, {scan?.total ?? '—'} found locally
            </span>
          </div>

          {scan && scan.found && scan.found.length > 0 && (
            <div className="mb-4 border border-border rounded-md max-h-64 overflow-y-auto">
              <table className="w-full text-xs">
                <thead className="bg-surface-tertiary sticky top-0">
                  <tr>
                    <th className="text-left p-2 w-8"></th>
                    <th className="text-left p-2">Project Folder</th>
                    <th className="text-left p-2">Session</th>
                    <th className="text-right p-2">Size</th>
                    <th className="text-right p-2">Modified</th>
                  </tr>
                </thead>
                <tbody>
                  {scan.found.map((f) => {
                    // The encoded folder name (`C--dev-ttx-VOICE-NVIDIA`)
                    // and the imported `project` field (cwd basename, e.g.
                    // `VOICE NVIDIA`) often differ — Claude Code's path
                    // encoding flattens slashes/colons but the cwd in the
                    // .jsonl preserves spaces and original casing. Look up
                    // the matching import and surface its project name so
                    // the user can see "yes this folder is the same as
                    // that imported project."
                    const matchedImport = imports.find(
                      (i) => i.id.split(':').slice(1).join(':') === f.session_id
                    )
                    const known = !!matchedImport
                    return (
                      <tr
                        key={f.path}
                        className={`border-t border-border ${known ? 'opacity-50' : ''}`}
                      >
                        <td className="p-2">
                          <input
                            type="checkbox"
                            checked={selectedPaths.has(f.path)}
                            onChange={() => toggleSelect(f.path)}
                            disabled={known}
                          />
                        </td>
                        <td className="p-2 text-text-secondary truncate max-w-xs">
                          {f.project_folder}
                          {known && matchedImport!.project &&
                              matchedImport!.project !== f.project_folder && (
                            <span className="block text-[10px] text-text-muted mt-0.5">
                              imported as: {matchedImport!.project}
                            </span>
                          )}
                        </td>
                        <td className="p-2 text-text-muted font-mono">
                          {f.session_id.slice(0, 8)}
                          {known && (
                            <span className="ml-2 text-success">✓ imported</span>
                          )}
                        </td>
                        <td className="p-2 text-right text-text-muted">
                          {fmtBytes(f.size_bytes)}
                        </td>
                        <td className="p-2 text-right text-text-muted">
                          {fmtRelative(f.mtime_iso)}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}

          {imports.length > 0 && (
            <div className="border border-border rounded-md max-h-72 overflow-y-auto">
              <table className="w-full text-xs">
                <thead className="bg-surface-tertiary sticky top-0">
                  <tr>
                    <th className="text-left p-2">Project</th>
                    <th className="text-left p-2">Session</th>
                    <th className="text-right p-2">Msgs</th>
                    <th className="text-right p-2">Duration</th>
                    <th className="text-right p-2">Size</th>
                    <th className="text-center p-2">Status</th>
                    <th className="text-right p-2"></th>
                  </tr>
                </thead>
                <tbody>
                  {imports.map((i) => (
                    <tr key={i.id} className="border-t border-border">
                      <td className="p-2 text-text-primary truncate max-w-xs">
                        {i.project || '(unknown)'}
                      </td>
                      <td className="p-2 text-text-muted font-mono">
                        {i.id.split(':')[1]?.slice(0, 8) || i.id}
                      </td>
                      <td className="p-2 text-right text-text-muted">
                        {i.message_count}
                      </td>
                      <td className="p-2 text-right text-text-muted">
                        {fmtDuration(i.duration_minutes)}
                      </td>
                      <td className="p-2 text-right text-text-muted">
                        {fmtBytes(i.bytes_size)}
                      </td>
                      <td className="p-2 text-center">
                        {i.processed ? (
                          <span className="text-success">summarized</span>
                        ) : (
                          <span className="text-text-muted">queued</span>
                        )}
                      </td>
                      <td className="p-2 text-right">
                        <button
                          onClick={() => handleDeleteImport(i.id)}
                          disabled={!!busy}
                          className="text-xs text-text-muted hover:text-red-400 cursor-pointer disabled:opacity-50"
                        >
                          ✕
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        {/* Loop + LLM */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <Card title="Background Loop">
            <dl className="text-xs space-y-1">
              <Row label="Running" value={loop?.running ? 'yes' : 'no'} />
              <Row label="Ticks run" value={loop?.ticks_run ?? 0} />
              <Row label="Failed" value={loop?.ticks_failed ?? 0} />
              <Row
                label="Last tick"
                value={fmtRelative(loop?.last_tick_at)}
              />
              <Row
                label="Started"
                value={fmtRelative(loop?.started_at)}
              />
              {loop?.last_error && (
                <Row label="Last error" value={loop.last_error} />
              )}
            </dl>
          </Card>

          <Card title="LLM Cost (last 7 days)">
            {!llmStats?.stats?.length ? (
              <div className="text-xs text-text-muted">No calls yet</div>
            ) : (
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-text-muted">
                    <th className="text-left">Backend</th>
                    <th className="text-right">Calls</th>
                    <th className="text-right">Avg ms</th>
                    <th className="text-right">Cost</th>
                  </tr>
                </thead>
                <tbody>
                  {llmStats.stats.map((s) => (
                    <tr key={s.actual_backend} className="border-t border-border">
                      <td className="py-1">{s.actual_backend}</td>
                      <td className="py-1 text-right">
                        {s.oks}/{s.calls}
                      </td>
                      <td className="py-1 text-right">{s.avg_ms}</td>
                      <td className="py-1 text-right">
                        ${(s.total_cost_usd ?? 0).toFixed(4)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>
        </div>
      </div>
      )}
    </div>
  )
}

// ── Sub-components ────────────────────────────────────────────

function BudgetIndicator({ budget }: { budget: BudgetSnapshot }) {
  const pctCost = budget.cost_max_usd > 0
    ? Math.min(100, Math.round((budget.cost_used_usd / budget.cost_max_usd) * 100))
    : 0
  const cls = budget.exhausted
    ? 'text-red-400'
    : pctCost > 70
      ? 'text-amber-400'
      : 'text-text-muted'
  return (
    <span className={`text-xs ${cls}`} title={`Daily cap: ${budget.cost_max_usd} / ${budget.calls_max} calls. Resets at UTC midnight.`}>
      Today: ${budget.cost_used_usd.toFixed(2)} / ${budget.cost_max_usd.toFixed(2)}
      {' '}({budget.calls_used}/{budget.calls_max} calls)
    </span>
  )
}

function NotificationsPanel({
  notifications,
  onDismiss,
  onDismissAll,
  onOpenInChat,
}: {
  notifications: NotificationRow[]
  onDismiss: (id: number) => void
  onDismissAll: () => void
  onOpenInChat: (n: NotificationRow) => void
}) {
  const unread = notifications.filter((n) => !n.dismissed_at)
  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-3xl mx-auto">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-semibold text-text-primary">
            Notifications ({unread.length} unread)
          </h3>
          {unread.length > 0 && (
            <button
              onClick={onDismissAll}
              className="px-3 py-1.5 rounded-md text-xs font-medium bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-primary cursor-pointer"
            >
              Dismiss all
            </button>
          )}
        </div>
        {unread.length === 0 ? (
          <div className="text-sm text-text-muted py-8 text-center">
            All caught up. Notifications will appear here when the overseer
            flags something — stale projects, automation anomalies, growing
            backlogs.
          </div>
        ) : (
          <ul className="space-y-2">
            {unread.map((n) => (
              <li
                key={n.id}
                className="rounded-lg border border-border bg-surface-secondary p-3 flex justify-between gap-3"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span
                      className={`px-1.5 py-0.5 rounded text-[10px] font-medium uppercase ${
                        n.severity === 'important'
                          ? 'bg-red-500/20 text-red-400'
                          : n.severity === 'warn'
                            ? 'bg-amber-500/20 text-amber-400'
                            : 'bg-text-muted/20 text-text-muted'
                      }`}
                    >
                      {n.severity}
                    </span>
                    <span className="text-sm text-text-primary font-medium">
                      {n.title}
                    </span>
                    <span className="text-xs text-text-muted ml-auto whitespace-nowrap">
                      {n.created_at?.slice(0, 16)}
                    </span>
                  </div>
                  {n.body && (
                    <p className="text-xs text-text-secondary mt-1.5 leading-relaxed">
                      {n.body}
                    </p>
                  )}
                  <div className="text-[10px] text-text-muted mt-1.5 font-mono">
                    rule={n.rule_name} · key={n.rule_key}
                  </div>
                  <div className="mt-2">
                    <button
                      onClick={() => onOpenInChat(n)}
                      className="px-2.5 py-1 rounded-md text-[11px] font-medium bg-accent/15 hover:bg-accent/25 text-accent-hover cursor-pointer"
                      title="Pre-fills the Chat tab with this notification's context so you can ask the overseer what to do"
                    >
                      Open in chat
                    </button>
                  </div>
                </div>
                <button
                  onClick={() => onDismiss(n.id)}
                  className="text-text-muted hover:text-text-primary text-lg leading-none cursor-pointer self-start"
                  title="Dismiss"
                >
                  ✕
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

function ChatPanel({
  messages,
  input,
  setInput,
  sending,
  onSend,
  onClear,
  onRefresh,
}: {
  messages: ChatMessage[]
  input: string
  setInput: (v: string) => void
  sending: boolean
  onSend: () => void
  onClear: () => void
  onRefresh: () => void
}) {
  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="flex items-center justify-between px-6 py-2 border-b border-border">
        <div className="text-xs text-text-muted">
          {messages.length} message{messages.length === 1 ? '' : 's'} · single ongoing thread
        </div>
        <div className="flex gap-2">
          <button
            onClick={onRefresh}
            disabled={sending}
            className="px-3 py-1 rounded-md text-xs font-medium bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-primary cursor-pointer disabled:opacity-50"
          >
            Refresh
          </button>
          <button
            onClick={onClear}
            disabled={sending || messages.length === 0}
            className="px-3 py-1 rounded-md text-xs font-medium text-text-muted hover:text-red-400 cursor-pointer disabled:opacity-50"
          >
            Clear thread
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-4">
        <div className="max-w-3xl mx-auto space-y-4">
          {messages.length === 0 ? (
            <div className="text-sm text-text-muted text-center py-12">
              Talk to the overseer. It has access to your working memory,
              recent gists, themes, and the institutional notes left by the
              first instance. Ask anything — what you've been working on,
              what you might be forgetting, what it thinks of a pattern it
              has noticed.
            </div>
          ) : (
            messages.map((m) => (
              <ChatBubble key={m.id} m={m} />
            ))
          )}
          {sending && (
            <div className="text-xs text-text-muted">
              <span className="inline-block animate-pulse">Thinking…</span>
            </div>
          )}
        </div>
      </div>

      <div className="border-t border-border px-6 py-3 bg-surface-secondary">
        <div className="max-w-3xl mx-auto flex gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                onSend()
              }
            }}
            placeholder="Type a message… (Enter to send, Shift+Enter for newline)"
            disabled={sending}
            rows={2}
            className="flex-1 rounded-md border border-border bg-surface-tertiary px-3 py-2 text-sm text-text-primary placeholder:text-text-muted resize-none focus:outline-none focus:border-accent disabled:opacity-50"
          />
          <button
            onClick={onSend}
            disabled={sending || !input.trim()}
            className="px-4 py-2 rounded-md text-sm font-medium bg-accent hover:bg-accent-hover text-white cursor-pointer disabled:opacity-50 self-end"
          >
            {sending ? 'Sending…' : 'Send'}
          </button>
        </div>
      </div>
    </div>
  )
}

function ChatBubble({ m }: { m: ChatMessage }) {
  const isUser = m.role === 'user'
  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-[80%] rounded-lg px-4 py-3 text-sm leading-relaxed whitespace-pre-wrap ${
          isUser
            ? 'bg-accent/15 text-text-primary border border-accent/30'
            : 'bg-surface-secondary text-text-primary border border-border'
        }`}
      >
        <div className="text-[10px] uppercase tracking-wide text-text-muted mb-1">
          {isUser ? 'you' : 'overseer'}
          {!isUser && m.model && (
            <span className="ml-2 normal-case">
              {m.model} · {m.latency_ms}ms · ${(m.cost_usd ?? 0).toFixed(4)}
            </span>
          )}
        </div>
        {m.content}
      </div>
    </div>
  )
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-border bg-surface-secondary p-4">
      <h3 className="text-sm font-semibold text-text-primary mb-3">{title}</h3>
      {children}
    </section>
  )
}

function StatCard({
  label,
  value,
  sub,
}: {
  label: string
  value: number | string
  sub?: string
}) {
  return (
    <div className="rounded-lg border border-border bg-surface-secondary p-3">
      <div className="text-xs text-text-muted uppercase tracking-wide">{label}</div>
      <div className="text-2xl font-semibold text-text-primary mt-1">{value}</div>
      {sub && <div className="text-xs text-text-muted mt-1">{sub}</div>}
    </div>
  )
}

function Row({ label, value }: { label: string; value: any }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-text-muted">{label}</dt>
      <dd className="text-text-primary text-right truncate max-w-xs">{String(value)}</dd>
    </div>
  )
}

function WorkingMemoryView({ wm }: { wm: WorkingMemory }) {
  return (
    <div className="space-y-4 text-xs">
      {wm.top_projects && wm.top_projects.length > 0 && (
        <div>
          <div className="text-text-muted uppercase tracking-wide mb-1">
            Top Projects ({wm.top_projects.length})
          </div>
          <ul className="space-y-1">
            {wm.top_projects.map((p) => (
              <li key={p.tag} className="flex justify-between">
                <span className="text-text-primary">{p.name || p.tag}</span>
                <span className="text-text-muted">
                  {p.last_touched?.slice(0, 10)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {wm.open_todos && wm.open_todos.length > 0 && (
        <div>
          <div className="text-text-muted uppercase tracking-wide mb-1">
            Open Reminders ({wm.open_todos.length})
          </div>
          <ul className="space-y-1 text-text-secondary">
            {wm.open_todos.slice(0, 8).map((t) => (
              <li key={t.id} className="truncate">
                • {t.content}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Slice 3f.5 #2: question-centered primary view (with evidence) */}
      {wm.top_questions && wm.top_questions.length > 0 ? (
        <div>
          <div className="text-text-muted uppercase tracking-wide mb-1">
            Open Questions ({wm.top_questions.length}) — primary axis
          </div>
          <ul className="space-y-3">
            {wm.top_questions.map((q) => (
              <li key={q.id} className="border-l-2 border-border pl-3">
                <div className="flex items-baseline gap-2">
                  <span className="text-text-muted text-[10px] uppercase">
                    [{q.confidence} · {q.lifecycle} · {q.evidence_count}ev]
                  </span>
                  <span className="text-text-primary font-medium">
                    {q.question}
                  </span>
                </div>
                {q.recent_evidence && q.recent_evidence.length > 0 && (
                  <ul className="mt-1 space-y-0.5">
                    {q.recent_evidence.slice(0, 3).map((ev, i) => {
                      const body =
                        ev.evidence_body || ev.reason || '(no body)'
                      const contribColor =
                        ev.contribution === 'complicates'
                          ? 'text-amber-400'
                          : ev.contribution === 'reframes'
                            ? 'text-accent-hover'
                            : ev.contribution === 'answers'
                              ? 'text-success'
                              : 'text-text-muted'
                      return (
                        <li
                          key={`${q.id}-${i}`}
                          className="text-[11px] text-text-secondary"
                        >
                          <span className={`mr-1 ${contribColor}`}>
                            ◆ [{ev.contribution}]
                          </span>
                          {body.length > 200
                            ? body.slice(0, 200) + '…'
                            : body}
                        </li>
                      )
                    })}
                  </ul>
                )}
              </li>
            ))}
          </ul>
        </div>
      ) : wm.open_questions && wm.open_questions.length > 0 ? (
        <div>
          <div className="text-text-muted uppercase tracking-wide mb-1">
            Open Questions ({wm.open_questions.length})
          </div>
          <ul className="space-y-1 text-text-secondary">
            {wm.open_questions.map((q) => (
              <li key={q.id}>
                <span className="text-text-muted text-[10px] mr-1">
                  [{q.confidence}]
                </span>
                {q.question}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {wm.unfiled_recent_gists && wm.unfiled_recent_gists.length > 0 && (
        <div>
          <div className="text-text-muted uppercase tracking-wide mb-1">
            Unfiled Recent Gists ({wm.unfiled_recent_gists.length})
            <span className="ml-2 normal-case text-[10px] italic">
              (didn't route to any open question — possibly a new
              question forming)
            </span>
          </div>
          <ul className="space-y-1 text-text-secondary text-[11px]">
            {wm.unfiled_recent_gists.slice(0, 5).map((g) => (
              <li key={g.id}>
                • {g.body.length > 200 ? g.body.slice(0, 200) + '…' : g.body}
              </li>
            ))}
          </ul>
        </div>
      )}

      {wm.recent_themes && wm.recent_themes.length > 0 && (
        <div>
          <div className="text-text-muted uppercase tracking-wide mb-1">
            Recent Themes ({wm.recent_themes.length})
          </div>
          <ul className="space-y-1 text-text-secondary">
            {wm.recent_themes.map((t) => (
              <li key={t.id}>{t.title}</li>
            ))}
          </ul>
        </div>
      )}

      {wm.recent_episode_titles && wm.recent_episode_titles.length > 0 && (
        <div>
          <div className="text-text-muted uppercase tracking-wide mb-1">
            Recent Episodes ({wm.recent_episode_titles.length})
          </div>
          <div className="text-text-secondary text-[11px]">
            {wm.recent_episode_titles.join(' · ')}
          </div>
        </div>
      )}

      {wm.last_week_digest && (
        <div>
          <div className="text-text-muted uppercase tracking-wide mb-1">
            Last Week Digest
          </div>
          <p className="text-text-secondary leading-relaxed">
            {wm.last_week_digest}
          </p>
        </div>
      )}
    </div>
  )
}

// ── Slice 3f.5 #3: Public Dialectic UI ──────────────────────

function DialecticPanel({
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

function DialecticList({
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

function DialecticRowView({
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
            {d.created_at?.slice(0, 16)}
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

// ── Slice 3f.5 #1: Journal viewer ───────────────────────────

function JournalPanel({
  entries,
  onRefresh,
}: {
  entries: JournalEntry[]
  onRefresh: () => void
}) {
  const reversed = [...entries].reverse() // newest first
  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-3xl mx-auto space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-base font-semibold text-text-primary">
              Overseer journal
            </h3>
            <p className="text-xs text-text-muted mt-1">
              The overseer's first-person reflections at the end of each
              notable tick. Append-only — these aren't for you, they're
              for future instances of the overseer to read at boot. You
              get to read along.
            </p>
          </div>
          <button
            onClick={onRefresh}
            className="px-3 py-1.5 rounded-md text-xs font-medium bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-primary cursor-pointer"
          >
            Refresh
          </button>
        </div>
        {reversed.length === 0 ? (
          <div className="text-sm text-text-muted py-12 text-center">
            No journal entries yet. They appear after the loop runs ticks
            with notable work (typically every 5 minutes when there's new
            data to chew on).
          </div>
        ) : (
          <ul className="space-y-3">
            {reversed.map((j) => (
              <JournalEntryView key={j.id} j={j} />
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

function JournalEntryView({ j }: { j: JournalEntry }) {
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
