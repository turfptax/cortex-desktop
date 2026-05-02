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

interface WorkingMemory {
  built_at?: string
  schema_version?: number
  top_projects?: Array<{
    tag: string
    name: string
    last_touched: string
    description?: string
  }>
  recent_decisions?: Array<{ id: number; content: string; created_at: string }>
  open_todos?: Array<{ id: number; content: string; created_at: string }>
  open_questions?: Array<{
    id: number
    question: string
    confidence: string
    tags?: string[]
  }>
  recent_themes?: Array<{ id: number; title: string; confidence: string }>
  recent_episode_titles?: string[]
  last_week_digest?: string
  future_overseer_notes_count?: number
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

type Tab = 'overview' | 'chat' | 'notifications'

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
  const [busy, setBusy] = useState<string>('')
  const [lastAction, setLastAction] = useState<string>('')
  const [error, setError] = useState<string>('')

  const refreshAll = async () => {
    setError('')
    try {
      const [s, w, l, ls, im, n, b] = await Promise.all([
        apiFetch<StatusResp>('/overseer/status'),
        apiFetch<WorkingMemoryResp>('/overseer/working-memory'),
        apiFetch<LoopResp>('/overseer/loop'),
        apiFetch<LlmStatsResp>('/overseer/llm/stats?days=7'),
        apiFetch<ImportsResp>('/overseer/imports?limit=200'),
        apiFetch<NotificationsResp>('/overseer/notifications'),
        apiFetch<BudgetResp>('/overseer/budget'),
      ])
      setStatus(s)
      setWm(w)
      setLoop(l)
      setLlmStats(ls)
      setImports(im.imports || [])
      setNotifications(n.notifications || [])
      setNotificationsUnread(n.unread_count || 0)
      setBudget(b.budget || null)
    } catch (e: any) {
      setError(`Refresh failed: ${e?.message || e}`)
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
        `Import: ${c.imported ?? 0} new, ${c.skipped ?? 0} skipped, ${c.failed ?? 0} failed (of ${c.requested ?? 0})`
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
                    const known = imports.some(
                      (i) => i.id.split(':').slice(1).join(':') === f.session_id
                    )
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
}: {
  notifications: NotificationRow[]
  onDismiss: (id: number) => void
  onDismissAll: () => void
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
                </div>
                <button
                  onClick={() => onDismiss(n.id)}
                  className="text-text-muted hover:text-text-primary text-lg leading-none cursor-pointer"
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

      {wm.open_questions && wm.open_questions.length > 0 && (
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
