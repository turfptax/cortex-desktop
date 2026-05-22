import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { apiFetch } from '../../lib/api'
import { fmtTime } from '../../lib/time'
import { ExplorerPanel, type GraphResp } from './ExplorerPanel'
import { ProjectsTab } from './ProjectsTab'
import { JournalTab } from './JournalTab'
import { EcosystemMapPanel } from './EcosystemMapPanel'
import { ActivityPanel } from './ActivityPanel'
import { useVoiceMode, type VoiceState } from '../../hooks/useVoiceMode'

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
  token?: string                 // 3g #2
}

interface WMTopQuestion {
  id: number
  token?: string                 // 3g #2
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
  token?: string                 // 3g #2
  body: string
  period_label?: string
  created_at?: string
}

// Slice 3g: depth signals
interface WMPattern {
  id: number
  token?: string
  name: string
  body: string
  confidence?: string
  occurrences?: number
  last_observed_at?: string
}

interface WMDrift {
  id: number
  token?: string
  body: string
  direction?: string
  confidence?: string
  observed_at?: string
}

interface WMFutureNote {
  id: number
  token?: string
  instance_id: string
  written_at?: string
  body: string
}

interface WMRollup {
  id?: number
  token?: string
  project: string
  rollup_date: string
  session_count?: number
  total_minutes?: number
  median_minutes?: number
  summary: string
}

// Slice 3g #2: drill-down detail response
interface DetailNextToken {
  token: string
  label: string
  kind?: string
}

interface DetailResp {
  ok: boolean
  token?: string
  type?: string
  primary?: Record<string, any>
  tags?: string[]
  context?: Record<string, any>
  next_tokens?: DetailNextToken[]
  error?: string
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
  recent_themes?: Array<{ id: number; token?: string; title: string; confidence: string }>
  recent_episode_titles?: string[]
  last_week_digest?: string
  unfiled_recent_gists?: WMUnfiledGist[]
  future_overseer_notes_count?: number
  journal_entry_count?: number
  blindspots?: BlindspotRow[]
  // Slice 3g: depth signals
  recent_patterns?: WMPattern[]
  recent_drift?: WMDrift[]
  recent_future_notes?: WMFutureNote[]
  recent_rollups?: WMRollup[]
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
  // Polish slice CP1: server-side authoritative flag
  file_hash?: string
  already_imported?: boolean
}

interface ScanResp {
  ok: boolean
  found?: ScanRow[]
  total?: number
  scanned_dir?: string
  note?: string
  already_imported_count?: number
  new_count?: number
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

// Slice 8: file attachments on chat
type ChatAttachmentKind = 'image' | 'text' | 'pdf' | 'other'

interface ChatAttachmentRef {
  // Returned from POST /api/overseer/chat/upload and submitted back
  // with POST /api/overseer/chat. The Pi reads bytes from pi_path.
  filename: string
  mime_type: string
  size: number
  pi_path: string
  file_id?: number | null
  sha256?: string
  kind?: ChatAttachmentKind
}

interface ChatStoredAttachment {
  // Shape returned by Pi on /chat/history per message (chat_message_files row)
  id: number
  chat_message_id: number
  filename: string
  mime_type: string
  size_bytes: number
  kind: ChatAttachmentKind
  pi_path: string
  file_id: number
  sha256: string
  created_at?: string
}

// Slice 9.5 CP1 (2026-05-19): the backend persists tool_calls + other
// per-message audit fields inside metadata_json. The frontend parses
// that string once at load time into structured fields so the chat
// rendering layer doesn't need to JSON.parse on every render.
interface ChatToolCallSummary {
  iter: number
  name: string
  args: Record<string, any>
  result_chars: number
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
  attachments?: ChatStoredAttachment[]   // Slice 8
  // Slice 9.5 CP1: parsed from metadata_json. None of these are
  // required — historic rows from before persistence won't have them.
  tool_calls?: ChatToolCallSummary[]
  tool_iterations?: number
  history_turns_used?: number
  context_chars?: number
  // raw_metadata kept for /compress and debug — full original JSON.
  raw_metadata?: Record<string, any>
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
  attachments?: ChatStoredAttachment[]   // Slice 8
}

interface ChatUploadResp {
  ok: boolean
  attachments: ChatAttachmentRef[]
  rejected: { filename: string; size: number; error: string }[]
  counts: { uploaded: number; rejected: number }
}

// Local state for a file that the user has dropped/picked but hasn't
// sent yet. Lifecycle: queued → uploading → ready (with `ref`) → sent.
// On error, stays in the composer with `error` set so the user can
// retry or remove it.
type PendingAttachmentStatus = 'queued' | 'uploading' | 'ready' | 'error'

interface PendingAttachment {
  localId: string
  file: File
  filename: string
  size: number
  mime_type: string
  status: PendingAttachmentStatus
  previewUrl?: string                   // ObjectURL for images, revoked on remove
  ref?: ChatAttachmentRef               // populated once upload succeeds
  error?: string
}

// Mirror the Hub-side allowlist (hub/backend/routers/overseer.py).
const CHAT_MAX_FILES = 10
const CHAT_MAX_FILE_BYTES = 5 * 1024 * 1024
const CHAT_ALLOWED_EXTS = new Set([
  '.txt', '.md', '.py', '.js', '.ts', '.tsx', '.jsx',
  '.json', '.yaml', '.yml', '.csv', '.log', '.html',
  '.css', '.sh', '.sql', '.toml', '.ini', '.env',
  '.png', '.jpg', '.jpeg', '.webp', '.gif', '.pdf',
])

function fileExt(name: string): string {
  const ix = name.lastIndexOf('.')
  return ix < 0 ? '' : name.slice(ix).toLowerCase()
}

function classifyKind(name: string, mime: string): ChatAttachmentKind {
  const ext = fileExt(name)
  if (mime.startsWith('image/') || ['.png', '.jpg', '.jpeg', '.webp', '.gif'].includes(ext)) return 'image'
  if (mime === 'application/pdf' || ext === '.pdf') return 'pdf'
  if (mime.startsWith('text/') || CHAT_ALLOWED_EXTS.has(ext)) return 'text'
  return 'other'
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

// Slice 9.6 CP1 (2026-05-19): custom action button shape attached to
// notifications by overseer's emit_notification tool. kind drives the
// frontend's interaction model: predefined CRUD names execute server-
// side immediately; 'free_text' opens a textarea inline; 'yes_no'
// renders as a two-button pair; 'dispatch_sibling' is a single button
// that just logs the response (overseer reads + acts next tick).
interface NotificationAction {
  label: string
  kind: string  // 'archive_project' | 'mark_dormant' | 'free_text' | 'yes_no' | 'dispatch_sibling' | custom
  payload?: Record<string, any>
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
  // 3i CP1: per-rule action state
  snoozed_until: string | null
  archived_at: string | null
  // 9.6 CP1: actions_json parsed into the array form
  actions?: NotificationAction[]
  // Raw string from API (parsed in handler)
  actions_json?: string
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
  token?: string                 // 3g #2
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

// ── Slice 3h: insight queue ─────────────────────────────────
interface PendingInterpretation {
  id: number
  kind: 'theme' | 'pattern' | 'drift' | 'blindspot'
  title: string
  body: string
  confidence: string
  direction: string
  rationale: string
  proposed_by: string
  proposed_at: string
  source_kind: string
  source_project: string
  source_window_start: string | null
  source_window_end: string | null
  source_pointer_ids: string  // JSON array
  source_chat_message_id: number | null
  status: 'pending' | 'confirmed' | 'rejected' | 'edited' | 'superseded'
  reviewed_at: string | null
  reviewed_by: string
  review_note: string
  edit_title: string
  edit_body: string
  applied_table: string
  applied_id: number | null
  // 3i CP2: blindspot-kind specific fields
  bs_model_pattern?: string
  bs_topic_pattern?: string
  bs_confidence_adjustment?: number
}

interface InsightScanRow {
  id: number
  scan_kind: string
  project: string
  window_start: string | null
  window_end: string | null
  gists_seen: number
  candidates_proposed: number
  candidates_deduped: number
  cost_usd: number
  triggered_by: string
  ok: number
  error: string
  scanned_at: string
}

interface InsightScansResp {
  ok: boolean
  scans?: InsightScanRow[]
}

// 3i CP1: project classification table
interface ProjectClassRow {
  project: string
  session_count: number
  avg_duration_minutes: number
  avg_messages: number
  total_messages: number
  last_seen: string | null
  treat_as: 'auto' | 'human' | 'automation' | 'ignore'
  manual_override: boolean
  classified_at: string | null
  classified_reason: string | null
  rollup_count: number
}

interface ProjectsListResp {
  ok: boolean
  projects?: ProjectClassRow[]
  total?: number
}

interface InsightPendingResp {
  ok: boolean
  interpretations?: PendingInterpretation[]
  counts?: { pending: number; confirmed: number; rejected: number; edited: number }
  error?: string
}

interface InsightScanResp {
  ok: boolean
  project?: string
  gists_seen?: number
  candidates_proposed?: number
  candidates_deduped?: number
  cost_usd?: number
  scan_id?: number
  note?: string
  error?: string
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

type Tab = 'overview' | 'chat' | 'dialectic' | 'journal' | 'insights' | 'projects' | 'classify' | 'notifications' | 'explorer' | 'ecosystem' | 'activity'

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
  // Slice 8: pending file attachments queued in the composer
  const [chatPending, setChatPending] = useState<PendingAttachment[]>([])
  const [notifications, setNotifications] = useState<NotificationRow[]>([])
  const [notificationsUnread, setNotificationsUnread] = useState<number>(0)
  const [budget, setBudget] = useState<BudgetSnapshot | null>(null)
  const [dialectics, setDialectics] = useState<DialecticRow[]>([])
  const [dialecticCounts, setDialecticCounts] = useState<DialecticListResp['counts'] | null>(null)
  const [journal, setJournal] = useState<JournalEntry[]>([])
  const [blindspots, setBlindspots] = useState<BlindspotRow[]>([])
  const [expandedDialecticId, setExpandedDialecticId] = useState<number | null>(null)
  const [expandedToken, setExpandedToken] = useState<string | null>(null)
  // Slice 3h: insight queue state
  const [insights, setInsights] = useState<PendingInterpretation[]>([])
  const [insightCounts, setInsightCounts] = useState<InsightPendingResp['counts']>(undefined)
  const [insightScanProject, setInsightScanProject] = useState<string>('')
  const [insightScanDays, setInsightScanDays] = useState<number>(7)
  const [insightStatusFilter, setInsightStatusFilter] = useState<string>('pending')
  const [editingInsightId, setEditingInsightId] = useState<number | null>(null)
  const [editTitle, setEditTitle] = useState<string>('')
  const [editBody, setEditBody] = useState<string>('')
  const [insightScansHistory, setInsightScansHistory] = useState<InsightScanRow[]>([])
  const [projects, setProjects] = useState<ProjectClassRow[]>([])
  const [projectFilter, setProjectFilter] = useState<string>('')
  // Polish CP1: Explorer tab state
  const [graph, setGraph] = useState<GraphResp | null>(null)
  const [graphLoading, setGraphLoading] = useState(false)
  const [graphError, setGraphError] = useState<string>('')
  // Polish CP3: per-project group expand state for the Imports panel
  const [openImportGroups, setOpenImportGroups] = useState<Set<string>>(new Set())
  const toggleImportGroup = (folder: string) => {
    setOpenImportGroups((prev) => {
      const next = new Set(prev)
      if (next.has(folder)) next.delete(folder)
      else next.add(folder)
      return next
    })
  }
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
      // Slice 9.6 CP1: parse actions_json string into structured
      // actions array so the render layer doesn't reparse per render.
      const parsedNotifs = (n.notifications || []).map((row) => {
        const raw = (row as any).actions_json
        if (typeof raw === 'string' && raw && raw !== '[]') {
          try {
            const parsed = JSON.parse(raw)
            if (Array.isArray(parsed)) return { ...row, actions: parsed }
          } catch {
            // bad json, fall through with empty actions
          }
        }
        return row
      })
      setNotifications(parsedNotifs)
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
      const r = await apiFetch<ChatHistoryResp & { messages?: (ChatMessage & { metadata_json?: string })[] }>(
        '/overseer/chat/history?limit=200',
      )
      // Slice 9.5 CP1: parse metadata_json once so the render layer
      // doesn't reparse on every render and so we get typed fields.
      const parsed: ChatMessage[] = (r.messages || []).map((m) => {
        const out: ChatMessage = { ...m }
        const raw = (m as any).metadata_json
        if (typeof raw === 'string' && raw && raw !== '{}') {
          try {
            const meta = JSON.parse(raw)
            out.raw_metadata = meta
            if (Array.isArray(meta.tool_calls)) {
              out.tool_calls = meta.tool_calls as ChatToolCallSummary[]
            }
            if (typeof meta.tool_iterations === 'number') {
              out.tool_iterations = meta.tool_iterations
            }
            if (typeof meta.history_turns_used === 'number') {
              out.history_turns_used = meta.history_turns_used
            }
            if (typeof meta.context_chars === 'number') {
              out.context_chars = meta.context_chars
            }
          } catch {
            // Bad JSON — leave fields unset, don't crash the render.
          }
        }
        return out
      })
      setChatMessages(parsed)
    } catch (e: any) {
      setError(`Chat refresh failed: ${e?.message || e}`)
    }
  }

  const refreshInsights = async (status: string = insightStatusFilter) => {
    try {
      const q = status ? `?status=${encodeURIComponent(status)}` : ''
      const [pending, scans] = await Promise.all([
        apiFetch<InsightPendingResp>(`/overseer/insight/pending${q}`),
        apiFetch<InsightScansResp>(`/overseer/insight/scans?limit=15`),
      ])
      setInsights(pending.interpretations || [])
      setInsightCounts(pending.counts)
      setInsightScansHistory(scans.scans || [])
    } catch (e: any) {
      setError(`Insights refresh failed: ${e?.message || e}`)
    }
  }

  const handleInsightScanNow = async () => {
    if (!insightScanProject.trim()) {
      setError('Pick a project tag to scan (e.g. UFOSINT)')
      return
    }
    setBusy(`Scanning ${insightScanProject}…`)
    setError('')
    setLastAction('')
    try {
      const r = await apiFetch<InsightScanResp>('/overseer/insight/scan-now', {
        method: 'POST',
        body: JSON.stringify({
          project: insightScanProject.trim(),
          days: insightScanDays,
        }),
      })
      if (!r.ok) {
        setError(`Scan failed: ${r.error || 'unknown'}`)
      } else if ((r.candidates_proposed || 0) === 0) {
        setLastAction(
          `Scan complete: ${r.gists_seen} gists seen, no new candidates ` +
          `(deduped: ${r.candidates_deduped ?? 0}, cost $${(r.cost_usd ?? 0).toFixed(4)}).` +
          (r.note ? ` ${r.note}` : ''),
        )
      } else {
        setLastAction(
          `Scan proposed ${r.candidates_proposed} candidates ` +
          `(${r.gists_seen} gists, $${(r.cost_usd ?? 0).toFixed(4)}).`,
        )
      }
      await refreshInsights('pending')
    } catch (e: any) {
      setError(`Scan failed: ${e?.message || e}`)
    } finally {
      setBusy('')
    }
  }

  const handleDistillCorrections = async () => {
    setBusy('Distilling corrections…')
    setError('')
    setLastAction('')
    try {
      const r = await apiFetch<{
        ok: boolean
        corrections_seen?: number
        candidates_proposed?: number
        candidates_deduped?: number
        cost_usd?: number
        note?: string
        error?: string
      }>('/overseer/insight/distill-corrections', {
        method: 'POST',
      })
      if (!r.ok) {
        setError(`Distill failed: ${r.error || 'unknown'}`)
      } else if ((r.candidates_proposed || 0) === 0) {
        setLastAction(
          `Distill: ${r.corrections_seen ?? 0} corrections seen, no new ` +
          `blindspot candidates (deduped: ${r.candidates_deduped ?? 0}, ` +
          `cost $${(r.cost_usd ?? 0).toFixed(4)})` +
          (r.note ? `. ${r.note}` : '.'),
        )
      } else {
        setLastAction(
          `Distill proposed ${r.candidates_proposed} blindspot candidate(s) ` +
          `from ${r.corrections_seen} corrections ($${(r.cost_usd ?? 0).toFixed(4)}).`,
        )
      }
      await refreshInsights('pending')
    } catch (e: any) {
      setError(`Distill failed: ${e?.message || e}`)
    } finally {
      setBusy('')
    }
  }

  const handleInsightDecide = async (
    id: number,
    decision: 'confirm' | 'reject' | 'edit-and-confirm',
    overrides: { edit_title?: string; edit_body?: string; review_note?: string } = {},
  ) => {
    setBusy(`Applying decision…`)
    setError('')
    try {
      const r = await apiFetch<{
        ok: boolean
        status?: string
        applied_table?: string
        applied_id?: number
        error?: string
      }>('/overseer/insight/decide', {
        method: 'POST',
        body: JSON.stringify({ id, decision, ...overrides }),
      })
      if (!r.ok) {
        setError(`Decide failed: ${r.error || 'unknown'}`)
      } else if (decision === 'reject') {
        setLastAction(`Rejected #${id}.`)
      } else if (r.applied_table) {
        setLastAction(
          `Confirmed #${id} → landed in ${r.applied_table}#${r.applied_id}.`,
        )
      } else {
        setLastAction(`#${id} → ${r.status}`)
      }
      setEditingInsightId(null)
      await refreshInsights(insightStatusFilter)
      // The working memory now contains a new pattern/drift/theme,
      // so refresh that too so it shows up in the Overview.
      try {
        const wmResp = await apiFetch<WorkingMemoryResp>('/overseer/working-memory')
        setWm(wmResp)
      } catch {}
    } catch (e: any) {
      setError(`Decide failed: ${e?.message || e}`)
    } finally {
      setBusy('')
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
    if (tab === 'insights') refreshInsights(insightStatusFilter)
    if (tab === 'projects') refreshProjects()
    if (tab === 'explorer' && !graph) refreshGraph()
  }, [tab])

  // Slice 8: validate one File against the local mirror of the Hub's
  // allowlist. Returns an error string on rejection or null if OK.
  const validatePending = (f: File): string | null => {
    const ext = fileExt(f.name)
    if (!CHAT_ALLOWED_EXTS.has(ext)) {
      return `unsupported file type (${ext || 'no extension'})`
    }
    if (f.size === 0) return 'empty file'
    if (f.size > CHAT_MAX_FILE_BYTES) {
      return `too large (${formatBytes(f.size)} > ${formatBytes(CHAT_MAX_FILE_BYTES)})`
    }
    return null
  }

  // Slice 8: enqueue dropped/picked files. Filters out anything that
  // would push past CHAT_MAX_FILES — the rejected files become inline
  // error messages so the user knows why some didn't take.
  const handleAddPendingFiles = (files: FileList | File[]) => {
    const list = Array.from(files)
    if (!list.length) return
    setError('')
    setChatPending((prev) => {
      const room = CHAT_MAX_FILES - prev.length
      if (room <= 0) {
        setError(`Already at max ${CHAT_MAX_FILES} attachments — remove one to add more.`)
        return prev
      }
      const incoming: PendingAttachment[] = []
      for (const f of list.slice(0, room)) {
        const err = validatePending(f)
        const kind = classifyKind(f.name, f.type)
        const previewUrl = (kind === 'image' && !err) ? URL.createObjectURL(f) : undefined
        incoming.push({
          localId: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          file: f,
          filename: f.name,
          size: f.size,
          mime_type: f.type || '',
          status: err ? 'error' : 'queued',
          previewUrl,
          error: err || undefined,
        })
      }
      if (list.length > room) {
        setError(`Only added ${room} files — ${list.length - room} skipped because the cap is ${CHAT_MAX_FILES}.`)
      }
      return [...prev, ...incoming]
    })
  }

  const handleRemovePending = (localId: string) => {
    setChatPending((prev) => {
      const target = prev.find((p) => p.localId === localId)
      if (target?.previewUrl) URL.revokeObjectURL(target.previewUrl)
      return prev.filter((p) => p.localId !== localId)
    })
  }

  // Upload the queued (and not-yet-uploaded, not-error) attachments
  // to the Hub via multipart. Returns the list of refs that succeeded
  // and mutates pending state to reflect upload status. Anything that
  // came back rejected from the Hub is marked with an error so the
  // user can see why it didn't ship.
  const uploadPendingAttachments = async (): Promise<ChatAttachmentRef[]> => {
    const toUpload = chatPending.filter(
      (p) => p.status === 'queued' && !p.error,
    )
    const ready = chatPending.filter((p) => p.status === 'ready' && p.ref)
    if (toUpload.length === 0) {
      return ready.map((p) => p.ref!).filter(Boolean)
    }
    // Mark uploading
    setChatPending((prev) =>
      prev.map((p) => (toUpload.find((t) => t.localId === p.localId)
        ? { ...p, status: 'uploading' as const } : p)),
    )
    const fd = new FormData()
    for (const p of toUpload) fd.append('files', p.file, p.filename)
    let resp: Response
    try {
      resp = await fetch('/api/overseer/chat/upload', {
        method: 'POST', body: fd,
      })
    } catch (e: any) {
      setChatPending((prev) =>
        prev.map((p) => (toUpload.find((t) => t.localId === p.localId)
          ? { ...p, status: 'error' as const, error: 'network: ' + (e?.message || e) }
          : p)),
      )
      throw new Error('Upload network failure')
    }
    if (!resp.ok) {
      const body = await resp.text()
      setChatPending((prev) =>
        prev.map((p) => (toUpload.find((t) => t.localId === p.localId)
          ? { ...p, status: 'error' as const, error: `HTTP ${resp.status}: ${body.slice(0, 120)}` }
          : p)),
      )
      throw new Error(`Upload failed: HTTP ${resp.status}`)
    }
    const data: ChatUploadResp = await resp.json()
    // Map server-returned attachments back to pending entries by filename
    // order (the Hub returns them in submission order).
    const accepted = data.attachments || []
    const rejected = data.rejected || []
    setChatPending((prev) => {
      const next = [...prev]
      let acceptedIx = 0
      let rejectedIx = 0
      for (const t of toUpload) {
        const slot = next.findIndex((p) => p.localId === t.localId)
        if (slot < 0) continue
        // Match by filename — server preserves the original name.
        const acc = accepted.find((a, i) => i === acceptedIx && a.filename === t.filename)
        if (acc) {
          next[slot] = { ...next[slot], status: 'ready', ref: acc, error: undefined }
          acceptedIx++
          continue
        }
        const rej = rejected[rejectedIx]
        if (rej) {
          next[slot] = { ...next[slot], status: 'error', error: rej.error }
          rejectedIx++
          continue
        }
        next[slot] = { ...next[slot], status: 'error', error: 'no server response' }
      }
      return next
    })
    return [...ready.map((p) => p.ref!).filter(Boolean), ...accepted]
  }

  // Slice 9.5 CP2 (2026-05-19): slash commands intercept chat input
  // before the message hits the overseer LLM. Saves the cost of a
  // chat-turn for simple state queries (cost / budget / insights /
  // working memory / sibling status) and gives Tory a fast keyboard
  // affordance for /clear / /compress / /tick / /help.
  // System bubbles use negative IDs so they never collide with real
  // chat_messages.id values from the DB.
  const slashSystemIdRef = useRef(-1)
  const pushSlashSystemMessage = (content: string) => {
    slashSystemIdRef.current -= 1
    const msg: ChatMessage = {
      id: slashSystemIdRef.current,
      role: 'system',
      content,
      created_at: new Date().toISOString(),
    }
    setChatMessages((prev) => [...prev, msg])
  }

  const SLASH_HELP_TEXT = [
    '## Slash commands',
    '',
    '- `/help` — show this list',
    '- `/clear` — wipe the chat thread (confirm prompt)',
    '- `/compress` — fold older turns into a compressed summary',
    '- `/cost` — show today\'s LLM spend and remaining budget',
    '- `/budget` — alias for /cost',
    '- `/tick` — force an overseer loop tick now',
    '- `/whoami` — working memory snapshot (freshness + posture)',
    '- `/insights` — list pending interpretations',
    '- `/sibling-status` — A-channel dispatch counters',
    '',
    'Commands run locally (no LLM cost). Overseer can still see results in their next turn.',
  ].join('\n')

  const handleSlashCommand = async (raw: string): Promise<boolean> => {
    const parts = raw.trim().split(/\s+/)
    const cmd = (parts[0] || '').toLowerCase()
    if (!cmd.startsWith('/')) return false
    setChatInput('')
    switch (cmd) {
      case '/help': {
        pushSlashSystemMessage(SLASH_HELP_TEXT)
        return true
      }
      case '/clear': {
        if (!confirm('Clear the entire chat thread? This cannot be undone.')) return true
        try {
          await apiFetch<any>('/overseer/chat/clear', { method: 'POST' })
          setChatMessages([])
          chatPending.forEach((p) => p.previewUrl && URL.revokeObjectURL(p.previewUrl))
          setChatPending([])
          setLastAction('Chat thread cleared')
        } catch (e: any) {
          pushSlashSystemMessage(`**/clear failed:** ${e?.message || e}`)
        }
        return true
      }
      case '/compress': {
        try {
          const r = await apiFetch<{
            ok: boolean
            messages_before: number
            messages_after: number
            compressed_summary?: string
            cost_usd?: number
            error?: string
          }>('/overseer/chat/compress', { method: 'POST', body: JSON.stringify({}) })
          if (!r.ok) {
            pushSlashSystemMessage(`**/compress failed:** ${r.error || 'unknown'}`)
          } else {
            pushSlashSystemMessage(
              `**Chat compressed.** ${r.messages_before} → ${r.messages_after} messages ` +
                `(\$${(r.cost_usd ?? 0).toFixed(4)}).\n\n` +
                (r.compressed_summary
                  ? `**Summary written into thread:**\n\n> ${r.compressed_summary.split('\n').join('\n> ')}`
                  : ''),
            )
            await refreshChat()
          }
        } catch (e: any) {
          pushSlashSystemMessage(`**/compress failed:** ${e?.message || e}`)
        }
        return true
      }
      case '/cost':
      case '/budget': {
        try {
          const r = await apiFetch<{
            ok: boolean
            daily?: {
              date: string
              cost_used_usd: number
              cost_max_usd: number
              calls_used: number
              calls_max: number
            }
            session?: { cost_used_usd: number; calls_used: number }
          }>('/overseer/budget')
          const d = r.daily || ({} as any)
          pushSlashSystemMessage(
            [
              '## Budget',
              '',
              `- **Today** (${d.date || '?'}): \$${(d.cost_used_usd ?? 0).toFixed(4)} / \$${(d.cost_max_usd ?? 0).toFixed(2)} ` +
                `· ${d.calls_used ?? 0} / ${d.calls_max ?? 0} calls`,
              `- **Session**: \$${(r.session?.cost_used_usd ?? 0).toFixed(4)} · ${r.session?.calls_used ?? 0} calls`,
            ].join('\n'),
          )
        } catch (e: any) {
          pushSlashSystemMessage(`**/cost failed:** ${e?.message || e}`)
        }
        return true
      }
      case '/tick': {
        try {
          const r = await apiFetch<any>('/overseer/tick-now', { method: 'POST', body: JSON.stringify({}) })
          const s = r.summary || {}
          pushSlashSystemMessage(
            [
              '## Tick complete',
              '',
              `- imports_summarized: ${s.imports_summarized ?? 0}`,
              `- notes_tagged: ${s.notes_tagged ?? 0}`,
              `- classify_changed: ${s.classify_changed ?? 0}`,
              `- working_memory_rebuilt: ${s.working_memory_rebuilt ? 'yes' : 'no'}`,
              `- errors: ${(s.errors || []).length || 0}`,
              `- finished: ${s.finished_at || '?'}`,
            ].join('\n'),
          )
        } catch (e: any) {
          pushSlashSystemMessage(`**/tick failed:** ${e?.message || e}`)
        }
        return true
      }
      case '/whoami': {
        try {
          const r = await apiFetch<{ working_memory?: any }>('/overseer/working-memory')
          const wm = r.working_memory || {}
          const built = wm.local_built_at || wm.built_at || '?'
          const lastGist = wm.last_successful_gist_at || '?'
          const queue = wm.import_queue_depth ?? 0
          const dist = wm.recent_gist_source_distribution || {}
          const sibT = wm.sibling_dispatched_today
          const sibC = wm.sibling_daily_cap
          const sibU = wm.sibling_unrated_count
          const sibP = wm.sibling_pending_for_me
          const gitState = wm.git_ingest || {}
          const gitLast = gitState.last_run_at || '?'
          const topByOrigin = Object.entries(dist.by_origin || {})
            .map(([k, v]) => `${k}: ${v}`)
            .join(', ')
          pushSlashSystemMessage(
            [
              '## Working memory snapshot',
              '',
              `- **Built**: ${built}`,
              `- **Last gist**: ${lastGist}`,
              `- **Ingest queue**: ${queue} unprocessed`,
              `- **Recent gists by origin**: ${topByOrigin || '(none)'}`,
              `- **Sibling (Cat A)**: ${sibT}/${sibC} today · unrated ${sibU} · pending-for-me ${sibP}`,
              `- **Git ingest**: last ran ${gitLast}`,
              `- **Open todos**: ${(wm.open_todos || []).length}`,
              `- **Open questions**: ${(wm.open_questions || []).length}`,
            ].join('\n'),
          )
        } catch (e: any) {
          pushSlashSystemMessage(`**/whoami failed:** ${e?.message || e}`)
        }
        return true
      }
      case '/insights': {
        try {
          const r = await apiFetch<{
            interpretations?: Array<{
              id: number
              kind: string
              title: string
              status: string
            }>
            counts?: Record<string, number>
          }>('/overseer/insight/pending?status=pending')
          const items = r.interpretations || []
          const lines = ['## Pending interpretations', '']
          if (items.length === 0) lines.push('_(none — all reviewed)_')
          else
            items.slice(0, 20).forEach((it) =>
              lines.push(`- **#${it.id}** [${it.kind}] ${it.title}`),
            )
          if (items.length > 20) lines.push('', `_(+${items.length - 20} more)_`)
          pushSlashSystemMessage(lines.join('\n'))
        } catch (e: any) {
          pushSlashSystemMessage(`**/insights failed:** ${e?.message || e}`)
        }
        return true
      }
      case '/sibling-status': {
        try {
          const r = await apiFetch<{ working_memory?: any }>('/overseer/working-memory')
          const wm = r.working_memory || {}
          pushSlashSystemMessage(
            [
              '## Sibling status (Category A)',
              '',
              `- Dispatched today: **${wm.sibling_dispatched_today ?? 0} / ${wm.sibling_daily_cap ?? 0}**`,
              `- Unrated completed (overseer owes a rating): **${wm.sibling_unrated_count ?? 0}**`,
              `- In flight (dispatched, awaiting result): **${wm.sibling_pending_for_me ?? 0}**`,
              '',
              '_Category B and C agents are gated on ≥1 week of A-volume data + graduation criteria._',
            ].join('\n'),
          )
        } catch (e: any) {
          pushSlashSystemMessage(`**/sibling-status failed:** ${e?.message || e}`)
        }
        return true
      }
      default: {
        pushSlashSystemMessage(`Unknown command \`${cmd}\`. Type \`/help\` for the list.`)
        return true
      }
    }
  }

  // Slice 14: voice-mode turn. Sends a spoken transcript through the
  // overseer chat with voice_mode=true (Pi appends the succinctness
  // directive), refreshes the thread so the bubbles render, and
  // returns the reply text for the hook to speak aloud.
  const handleVoiceTurn = async (text: string): Promise<string> => {
    const optimistic: ChatMessage = {
      id: -Date.now(),
      role: 'user',
      content: text,
      created_at: new Date().toISOString(),
    }
    setChatMessages((prev) => [...prev, optimistic])
    try {
      const r = await apiFetch<ChatSendResp>('/overseer/chat', {
        method: 'POST',
        body: JSON.stringify({ message: text, voice_mode: true }),
      })
      await refreshChat()
      if (!r.ok) {
        setError(`Voice chat error: ${r.error || 'unknown'}`)
        return ''
      }
      return r.reply || ''
    } catch (e: any) {
      setError(`Voice chat failed: ${e?.message || e}`)
      return ''
    }
  }

  const voice = useVoiceMode({ sendVoiceTurn: handleVoiceTurn })

  const handleSendChat = async () => {
    const message = chatInput.trim()
    // Slice 9.5 CP2: slash command intercept BEFORE the cost-of-a-turn
    // gate. Falls through to the LLM only for non-slash messages.
    if (message.startsWith('/')) {
      await handleSlashCommand(message)
      return
    }
    const queuedReady = chatPending.filter((p) => p.status === 'queued' && !p.error)
    const alreadyReady = chatPending.filter((p) => p.status === 'ready' && p.ref)
    const hasFiles = queuedReady.length + alreadyReady.length > 0
    if ((!message && !hasFiles) || chatSending) return

    setChatSending(true)
    setError('')

    // Step 1: upload any pending attachments first. If the user has
    // already retried after errors, only the still-queued ones go up
    // again. We block the send if uploads fail outright (network /
    // HTTP error) so the user can decide whether to remove or retry.
    let attachmentRefs: ChatAttachmentRef[] = []
    if (hasFiles) {
      try {
        attachmentRefs = await uploadPendingAttachments()
      } catch (e: any) {
        setError(`Attachment upload failed: ${e?.message || e}`)
        setChatSending(false)
        return
      }
      if (attachmentRefs.length === 0) {
        setError('All attachments were rejected — see errors on each file.')
        setChatSending(false)
        return
      }
    }

    // Step 2: optimistic user-message bubble locally (with attachment
    // previews so the user sees what they're sending immediately).
    const optimistic: ChatMessage = {
      id: -Date.now(),
      role: 'user',
      content: message || (attachmentRefs.length
        ? `(see attached file${attachmentRefs.length === 1 ? '' : 's'})` : ''),
      created_at: new Date().toISOString(),
      attachments: attachmentRefs.map((r, i) => ({
        id: -i - 1,
        chat_message_id: -Date.now(),
        filename: r.filename, mime_type: r.mime_type,
        size_bytes: r.size, kind: r.kind || classifyKind(r.filename, r.mime_type),
        pi_path: r.pi_path, file_id: r.file_id ?? 0,
        sha256: r.sha256 || '',
      })),
    }
    setChatMessages((prev) => [...prev, optimistic])
    setChatInput('')

    // Clear pending — ObjectURLs get revoked so we don't leak.
    chatPending.forEach((p) => p.previewUrl && URL.revokeObjectURL(p.previewUrl))
    setChatPending([])

    try {
      const body: any = { message: message || '' }
      if (attachmentRefs.length) body.attachments = attachmentRefs
      const r = await apiFetch<ChatSendResp>('/overseer/chat', {
        method: 'POST',
        body: JSON.stringify(body),
      })
      if (!r.ok) setError(`Chat error: ${r.error || 'unknown'}`)
      // Always re-fetch so we get the persisted IDs + assistant reply +
      // attachments-from-DB (replaces the optimistic stub above).
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
      // Clear any in-flight composer attachments too.
      chatPending.forEach((p) => p.previewUrl && URL.revokeObjectURL(p.previewUrl))
      setChatPending([])
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

  const refreshProjects = async () => {
    try {
      const r = await apiFetch<ProjectsListResp>('/overseer/projects')
      setProjects(r.projects || [])
    } catch (e: any) {
      setError(`Projects refresh failed: ${e?.message || e}`)
    }
  }

  const refreshGraph = async () => {
    setGraphLoading(true)
    setGraphError('')
    try {
      const r = await apiFetch<GraphResp>('/overseer/explorer/graph')
      if (!r.ok) {
        setGraphError(r.error || 'graph fetch failed')
      } else {
        setGraph(r)
      }
    } catch (e: any) {
      setGraphError(e?.message || String(e))
    } finally {
      setGraphLoading(false)
    }
  }

  const handleSetProjectClass = async (
    project: string,
    treat_as: 'auto' | 'human' | 'automation' | 'ignore',
  ) => {
    try {
      await apiFetch<any>('/overseer/projects/setting', {
        method: 'POST',
        body: JSON.stringify({ project, treat_as }),
      })
      await refreshProjects()
      setLastAction(
        `Set ${project} → ${treat_as}` +
        (treat_as === 'auto' ? ' (cleared override)' : ''),
      )
    } catch (e: any) {
      setError(`Project update failed: ${e?.message || e}`)
    }
  }

  const handleClassifyNow = async () => {
    setBusy('Classifying…')
    try {
      const r = await apiFetch<{ ok: boolean; changes?: any }>(
        '/overseer/projects/classify',
        { method: 'POST' },
      )
      if (r.ok) {
        const changed = (r.changes && r.changes.changed) || 0
        setLastAction(`Classifier ran. ${changed} project(s) changed.`)
      }
      await refreshProjects()
    } catch (e: any) {
      setError(`Classify-now failed: ${e?.message || e}`)
    } finally {
      setBusy('')
    }
  }

  // Slice 9.6 CP1: respond to a custom action button on a notification
  // (free_text / yes_no / dispatch_sibling / predefined CRUD kind). The
  // response is logged in notification_responses and surfaced to
  // overseer on next tick via the freshness block + the
  // get_pending_notification_responses tool.
  const handleNotificationRespond = async (
    notification_id: number,
    action_kind: string,
    action_label: string,
    response_payload: Record<string, any> = {},
    also_archive: boolean = true,
  ) => {
    try {
      await apiFetch<any>('/overseer/notifications/respond', {
        method: 'POST',
        body: JSON.stringify({
          notification_id,
          action_kind,
          action_label,
          response_payload,
          also_archive,
        }),
      })
      await refreshAll()
      setLastAction(
        `Responded to #${notification_id} (${action_kind})${
          also_archive ? ' + archived' : ''
        }`,
      )
    } catch (e: any) {
      setError(`Notification respond failed: ${e?.message || e}`)
    }
  }

  const handleNotificationAction = async (
    id: number,
    action: 'archive' | 'snooze' | 'touch',
    snooze_days: number = 30,
  ) => {
    try {
      const body: { id: number; action: string; snooze_days?: number } = {
        id, action,
      }
      if (action === 'snooze') body.snooze_days = snooze_days
      await apiFetch<any>('/overseer/notifications/action', {
        method: 'POST',
        body: JSON.stringify(body),
      })
      await refreshAll()
      if (action === 'snooze') {
        setLastAction(`Snoozed #${id} for ${snooze_days}d`)
      } else if (action === 'archive') {
        setLastAction(`Archived #${id}`)
      } else {
        setLastAction(`Brought #${id} back to top`)
      }
    } catch (e: any) {
      setError(`Notification ${action} failed: ${e?.message || e}`)
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
      // Polish CP3: use the SERVER-side already_imported flag instead
      // of joining against the client-side imports list (which is
      // capped at 200 rows and was wrongly auto-selecting ~250 already-
      // imported files for users with 200+ imports). The polish CP1
      // scan endpoint hashes each file and marks already_imported
      // authoritatively.
      const sel = new Set<string>()
      for (const f of r.found || []) {
        if (!f.already_imported) sel.add(f.path)
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
                ['insights', `Insights${insightCounts && insightCounts.pending > 0 ? ` (${insightCounts.pending})` : ''}`],
                ['projects', 'Projects'],
                ['classify', 'Classify'],
                ['explorer', 'Explorer'],
                ['ecosystem', 'Map'],
                ['activity', 'Activity'],
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
          pending={chatPending}
          onAddFiles={handleAddPendingFiles}
          onRemovePending={handleRemovePending}
          voiceState={voice.voiceState}
          voiceError={voice.lastError}
          voiceLastHeard={voice.lastHeard}
          onEnterVoice={voice.enterVoiceMode}
          onExitVoice={voice.exitVoiceMode}
        />
      )}
      {tab === 'notifications' && (
        <NotificationsPanel
          notifications={notifications}
          onDismiss={handleDismissNotification}
          onDismissAll={handleDismissAllNotifications}
          onOpenInChat={handleOpenNotificationInChat}
          onAction={handleNotificationAction}
          onRespond={handleNotificationRespond}
        />
      )}
      {tab === 'explorer' && (
        <ExplorerPanel
          graph={graph}
          loading={graphLoading}
          error={graphError}
          onRefresh={refreshGraph}
          onTokenClick={(t) => {
            // Same UX as Insights: jump to Overview, open DetailCard.
            setTab('overview')
            setExpandedToken(t)
          }}
        />
      )}
      {tab === 'ecosystem' && (
        <EcosystemMapPanel />
      )}
      {tab === 'activity' && (
        <ActivityPanel />
      )}
      {tab === 'projects' && (
        <ProjectsTab />
      )}
      {tab === 'classify' && (
        <ProjectsPanel
          projects={projects}
          filter={projectFilter}
          setFilter={setProjectFilter}
          onSetClass={handleSetProjectClass}
          onClassifyNow={handleClassifyNow}
          onRefresh={refreshProjects}
          busy={busy}
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
        <JournalTab
          overseerEntries={journal}
          onRefreshOverseerJournal={refreshJournal}
        />
      )}
      {tab === 'insights' && (
        <InsightsPanel
          interpretations={insights}
          counts={insightCounts}
          scans={insightScansHistory}
          statusFilter={insightStatusFilter}
          setStatusFilter={(s) => {
            setInsightStatusFilter(s)
            refreshInsights(s)
          }}
          scanProject={insightScanProject}
          setScanProject={setInsightScanProject}
          scanDays={insightScanDays}
          setScanDays={setInsightScanDays}
          onScanNow={handleInsightScanNow}
          onDistillCorrections={handleDistillCorrections}
          onDecide={handleInsightDecide}
          editingId={editingInsightId}
          setEditing={(id, title, body) => {
            setEditingInsightId(id)
            setEditTitle(title)
            setEditBody(body)
          }}
          editTitle={editTitle}
          editBody={editBody}
          setEditTitle={setEditTitle}
          setEditBody={setEditBody}
          onTokenClick={(t) => {
            setTab('overview')
            setExpandedToken(t)
          }}
          busy={busy}
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
            <WorkingMemoryView
              wm={wm.working_memory}
              expandedToken={expandedToken}
              onTokenClick={(t) =>
                setExpandedToken((prev) => (prev === t ? null : t))
              }
              onCloseDetail={() => setExpandedToken(null)}
            />
          )}
        </Card>

        {/* Imports — visual refresh in Polish CP3.
            Renamed from "Imported Claude Sessions" once the historical
            ChatGPT bulk import landed (1,728 sessions); the panel now
            covers any AI conversation source. The Scan flow remains
            Claude-Code specific (~/.claude/projects/) — other sources
            come in via direct import paths. */}
        <Card title="Imported AI Conversations">
          {/* Top action row + summary stat-line */}
          <div className="flex items-center gap-2 mb-4 flex-wrap">
            <button
              onClick={handleScan}
              disabled={!!busy}
              className="px-3.5 py-2 rounded-md text-xs font-medium bg-accent hover:bg-accent-hover text-white cursor-pointer disabled:opacity-50 transition-colors"
            >
              {scan ? 'Re-scan' : 'Scan ~/.claude/projects/'}
            </button>
            <button
              onClick={handleImportSelected}
              disabled={!!busy || selectedPaths.size === 0}
              className="px-3.5 py-2 rounded-md text-xs font-medium bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-primary cursor-pointer disabled:opacity-30 transition-colors"
            >
              Import{selectedPaths.size > 0 ? ` ${selectedPaths.size} selected` : ''}
            </button>
            <div className="ml-auto flex items-center gap-3 text-[11px]">
              <span className="text-text-muted">
                <span className="text-text-primary font-medium">{imports.length}</span> on Pi
              </span>
              {scan && (
                <>
                  <span className="text-text-muted">·</span>
                  <span className="text-success">
                    <span className="font-medium">{scan.new_count ?? 0}</span> new
                  </span>
                  <span className="text-text-muted">/</span>
                  <span className="text-text-muted">
                    <span className="text-text-secondary font-medium">{scan.already_imported_count ?? 0}</span> already imported
                  </span>
                  <span className="text-text-muted">(of {scan.total ?? '—'} local)</span>
                </>
              )}
            </div>
          </div>

          {/* Empty state when scan hasn't run */}
          {!scan && (
            <div className="border border-dashed border-border rounded-lg p-6 text-center text-xs text-text-muted">
              Scan to see local Claude Code sessions on this machine.
              The Pi will hash them, mark which are already imported, and let
              you pick which new ones to upload.
              <div className="mt-2 text-[11px] text-text-muted/70">
                Other sources (ChatGPT, etc.) come in via direct import paths
                and appear in the table below tagged with their source.
              </div>
            </div>
          )}

          {scan && scan.found && scan.found.length > 0 && (() => {
            // Polish CP3: group scanned files by project_folder so a
            // 430-file scan reads as ~30 collapsed groups instead of
            // a flat scroll. Each group exposes "select all new" for
            // the common case ("import all new sessions in Cortex").
            const groups = scan.found.reduce<Map<string, ScanRow[]>>((acc, f) => {
              const k = f.project_folder || '(unknown)'
              const list = acc.get(k) || []
              list.push(f)
              acc.set(k, list)
              return acc
            }, new Map())
            // Sort: groups with new items first, then by row count desc
            const sorted = Array.from(groups.entries()).sort((a, b) => {
              const newA = a[1].filter((f) => !f.already_imported).length
              const newB = b[1].filter((f) => !f.already_imported).length
              if (newA !== newB) return newB - newA
              return b[1].length - a[1].length
            })
            return (
              <ul className="border border-border rounded-lg overflow-hidden divide-y divide-border max-h-[28rem] overflow-y-auto">
                {sorted.map(([folder, rows]) => {
                  const newRows = rows.filter((f) => !f.already_imported)
                  const knownCount = rows.length - newRows.length
                  const allNewSelected = newRows.length > 0 &&
                    newRows.every((f) => selectedPaths.has(f.path))
                  const isOpen = openImportGroups.has(folder)
                  // Use the imported project name (cwd basename) when
                  // available — that's typically the cleaner display
                  // name compared to the encoded folder.
                  const matchedImport = imports.find(
                    (i) => i.project && rows.some(
                      (r) => i.id.split(':').slice(1).join(':') === r.session_id,
                    ),
                  )
                  const displayName = matchedImport?.project || folder
                  return (
                    <li key={folder} className="bg-surface-secondary/30 transition-colors hover:bg-surface-secondary/60">
                      {/* Group header — entire row toggles the group.
                          The "Select all new" button stops propagation
                          so it doesn't accidentally collapse the group
                          when the user means to bulk-select. */}
                      <div
                        onClick={() => toggleImportGroup(folder)}
                        className="px-3 py-2.5 flex items-center gap-2 flex-wrap cursor-pointer select-none"
                      >
                        <span
                          className={`text-text-muted text-[11px] tracking-wide w-5 inline-block transition-transform ${
                            isOpen ? 'rotate-90' : ''
                          }`}
                        >
                          ▸
                        </span>
                        <span className="text-sm text-text-primary font-medium truncate max-w-md">
                          {displayName}
                        </span>
                        {displayName !== folder && (
                          <span className="text-[10px] text-text-muted/60 font-mono truncate max-w-xs">
                            {folder}
                          </span>
                        )}
                        <div className="ml-auto flex items-center gap-1.5 text-[10px]">
                          {newRows.length > 0 && (
                            <span className="px-2 py-0.5 rounded-full bg-success/15 text-success font-semibold uppercase tracking-wide">
                              {newRows.length} new
                            </span>
                          )}
                          {knownCount > 0 && (
                            <span className="px-2 py-0.5 rounded-full bg-text-muted/10 text-text-muted/80 uppercase tracking-wide">
                              {knownCount} on Pi
                            </span>
                          )}
                          {newRows.length > 0 && (
                            <button
                              onClick={(e) => {
                                e.stopPropagation()
                                const next = new Set(selectedPaths)
                                if (allNewSelected) {
                                  newRows.forEach((f) => next.delete(f.path))
                                } else {
                                  newRows.forEach((f) => next.add(f.path))
                                }
                                setSelectedPaths(next)
                              }}
                              className="text-[10px] uppercase tracking-wide text-accent-hover hover:text-accent cursor-pointer ml-1.5 px-2 py-0.5 rounded hover:bg-accent/10 transition-colors"
                            >
                              {allNewSelected ? 'Deselect new' : 'Select all new'}
                            </button>
                          )}
                        </div>
                      </div>

                      {/* Group body — only when expanded */}
                      {isOpen && (
                        <ul className="border-t border-border bg-surface/30">
                          {rows.map((f) => {
                            const known = !!f.already_imported
                            const matched = known
                              ? imports.find(
                                  (i) =>
                                    i.id.split(':').slice(1).join(':') === f.session_id,
                                )
                              : undefined
                            return (
                              <li
                                key={f.path}
                                className={`pl-9 pr-3 py-1.5 flex items-center gap-3 text-[11px] transition-colors ${
                                  known
                                    ? 'opacity-50 hover:bg-surface-tertiary/20'
                                    : 'hover:bg-accent/5'
                                }`}
                              >
                                <input
                                  type="checkbox"
                                  checked={selectedPaths.has(f.path)}
                                  onChange={() => toggleSelect(f.path)}
                                  disabled={known}
                                  className="shrink-0 accent-accent cursor-pointer disabled:cursor-not-allowed"
                                />
                                <span className="text-text-muted/70 font-mono shrink-0 w-20">
                                  {f.session_id.slice(0, 8)}
                                </span>
                                {known ? (
                                  <span className="shrink-0 text-[9px] uppercase tracking-wider text-text-muted/70">
                                    on Pi
                                  </span>
                                ) : (
                                  <span className="shrink-0 text-[9px] uppercase tracking-wider text-success font-semibold">
                                    new
                                  </span>
                                )}
                                {matched?.project && matched.project !== folder && (
                                  <span className="text-[10px] text-text-muted truncate max-w-xs italic">
                                    → {matched.project}
                                  </span>
                                )}
                                <span className="ml-auto text-text-muted/70 shrink-0">
                                  {fmtBytes(f.size_bytes)}
                                </span>
                                <span className="text-text-muted/70 shrink-0 w-20 text-right">
                                  {fmtRelative(f.mtime_iso)}
                                </span>
                              </li>
                            )
                          })}
                        </ul>
                      )}
                    </li>
                  )
                })}
              </ul>
            )
          })()}

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
                        <div className="flex items-center gap-1.5">
                          <SourceBadge source={i.source} />
                          <span className="truncate">
                            {i.project || '(unknown)'}
                          </span>
                        </div>
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
  onAction,
  onRespond,
}: {
  notifications: NotificationRow[]
  onDismiss: (id: number) => void
  onDismissAll: () => void
  onOpenInChat: (n: NotificationRow) => void
  onAction: (
    id: number,
    action: 'archive' | 'snooze' | 'touch',
    snooze_days?: number,
  ) => void
  // Slice 9.6 CP1: respond to a custom action button. Distinct from
  // onAction (which targets the built-in archive/snooze/touch flow).
  onRespond: (
    notification_id: number,
    action_kind: string,
    action_label: string,
    response_payload?: Record<string, any>,
    also_archive?: boolean,
  ) => void
}) {
  const unread = notifications.filter(
    (n) => !n.dismissed_at && !n.archived_at,
  )

  // Polish CP2: group by rule_name. Single-row groups (e.g. one
  // import_backlog notification) render flat; multi-row groups
  // collapse into a summary header you can expand.
  const groups = unread.reduce<Map<string, NotificationRow[]>>(
    (acc, n) => {
      const k = n.rule_name || 'unknown'
      const list = acc.get(k) || []
      list.push(n)
      acc.set(k, list)
      return acc
    },
    new Map(),
  )
  // Sort groups: most-severe-then-largest first, so 'important' rules
  // and big stale clusters surface above noise.
  const sortedGroups = Array.from(groups.entries()).sort((a, b) => {
    const sevRank = (g: NotificationRow[]) => {
      if (g.some((n) => n.severity === 'important')) return 2
      if (g.some((n) => n.severity === 'warn')) return 1
      return 0
    }
    return sevRank(b[1]) - sevRank(a[1]) || b[1].length - a[1].length
  })

  // Per-group expanded state.
  const [openGroups, setOpenGroups] = useState<Set<string>>(new Set())
  const toggle = (k: string) => {
    setOpenGroups((prev) => {
      const next = new Set(prev)
      if (next.has(k)) next.delete(k)
      else next.add(k)
      return next
    })
  }

  const handleArchiveGroup = (rows: NotificationRow[]) => {
    rows.forEach((n) => onAction(n.id, 'archive'))
  }
  const handleSnoozeGroup = (rows: NotificationRow[]) => {
    rows.forEach((n) => onAction(n.id, 'snooze', 30))
  }

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-3xl mx-auto">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-semibold text-text-primary">
            Notifications ({unread.length} unread, {groups.size} rule
            {groups.size === 1 ? '' : 's'})
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
            {sortedGroups.map(([rule_name, rows]) => {
              const isOpen = openGroups.has(rule_name)
              const isSingle = rows.length === 1
              // Severity for the group = highest severity of any row.
              const sev = rows.some((n) => n.severity === 'important')
                ? 'important'
                : rows.some((n) => n.severity === 'warn')
                  ? 'warn'
                  : 'info'
              const sevClass =
                sev === 'important'
                  ? 'bg-red-500/20 text-red-400'
                  : sev === 'warn'
                    ? 'bg-amber-500/20 text-amber-400'
                    : 'bg-text-muted/20 text-text-muted'
              return (
                <li
                  key={rule_name}
                  className="rounded-lg border border-border bg-surface-secondary"
                >
                  {/* Group header — always shown */}
                  <div className="p-3 flex items-baseline gap-2 flex-wrap">
                    <span
                      className={`px-1.5 py-0.5 rounded text-[10px] font-medium uppercase ${sevClass}`}
                    >
                      {sev}
                    </span>
                    <span className="text-sm text-text-primary font-medium font-mono">
                      {rule_name}
                    </span>
                    <span className="text-xs text-text-muted">
                      ({rows.length} {rows.length === 1 ? 'row' : 'rows'})
                    </span>
                    {!isSingle && (
                      <button
                        onClick={() => toggle(rule_name)}
                        className="text-[10px] uppercase tracking-wide text-text-muted hover:text-text-primary cursor-pointer ml-1"
                      >
                        {isOpen ? '▾ collapse' : '▸ expand'}
                      </button>
                    )}
                    <div className="ml-auto flex items-center gap-1 flex-wrap">
                      {!isSingle && (
                        <>
                          <button
                            onClick={() => handleArchiveGroup(rows)}
                            className="px-2 py-0.5 rounded text-[10px] font-medium bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-secondary cursor-pointer"
                            title={`Archive all ${rows.length} ${rule_name} notifications`}
                          >
                            Archive all
                          </button>
                          <button
                            onClick={() => handleSnoozeGroup(rows)}
                            className="px-2 py-0.5 rounded text-[10px] font-medium bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-secondary cursor-pointer"
                            title={`Snooze all ${rows.length} for 30 days`}
                          >
                            Snooze all 30d
                          </button>
                        </>
                      )}
                    </div>
                  </div>

                  {/* For single-row groups, render the single notification
                      inline (no separate expand-to-see). For multi-row,
                      show body when expanded. */}
                  {(isSingle || isOpen) && (
                    <ul className="border-t border-border divide-y divide-border">
                      {rows.map((n) => (
                        <li key={n.id} className="p-3 flex justify-between gap-3">
                          <div className="flex-1 min-w-0">
                            <div className="flex items-baseline gap-2">
                              <span className="text-sm text-text-primary font-medium">
                                {n.title}
                              </span>
                              <span className="text-xs text-text-muted ml-auto whitespace-nowrap" title={fmtTime((n as any).local_created_at, n.created_at)}>
                                {fmtTime((n as any).local_created_at, n.created_at)}
                              </span>
                            </div>
                            {n.body && (
                              <p className="text-xs text-text-secondary mt-1.5 leading-relaxed">
                                {n.body}
                              </p>
                            )}
                            <div className="text-[10px] text-text-muted mt-1.5 font-mono">
                              key={n.rule_key}
                            </div>
                            {/* Slice 9.6 CP1 (2026-05-19): custom action
                                buttons attached by overseer when the
                                notification was emitted. Above the
                                standard Open/Archive/Snooze/Touch row
                                so they're the visually-primary CTA. */}
                            {n.actions && n.actions.length > 0 && (
                              <NotificationCustomActions
                                notification={n}
                                onRespond={onRespond}
                              />
                            )}
                            <div className="mt-2 flex items-center gap-2 flex-wrap">
                              <button
                                onClick={() => onOpenInChat(n)}
                                className="px-2.5 py-1 rounded-md text-[11px] font-medium bg-accent/15 hover:bg-accent/25 text-accent-hover cursor-pointer"
                                title="Pre-fills the Chat tab with this notification's context"
                              >
                                Open in chat
                              </button>
                              <button
                                onClick={() => onAction(n.id, 'archive')}
                                className="px-2.5 py-1 rounded-md text-[11px] font-medium bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-secondary cursor-pointer"
                                title="Acknowledge and hide permanently"
                              >
                                Archive
                              </button>
                              <button
                                onClick={() => onAction(n.id, 'snooze', 30)}
                                className="px-2.5 py-1 rounded-md text-[11px] font-medium bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-secondary cursor-pointer"
                                title="Hide for 30 days"
                              >
                                Snooze 30d
                              </button>
                              <button
                                onClick={() => onAction(n.id, 'touch')}
                                className="px-2.5 py-1 rounded-md text-[11px] font-medium text-text-muted hover:text-text-primary cursor-pointer"
                                title="Pull back to actionable queue"
                              >
                                Touch
                              </button>
                            </div>
                          </div>
                          <button
                            onClick={() => onDismiss(n.id)}
                            className="text-text-muted hover:text-text-primary text-lg leading-none cursor-pointer self-start"
                            title="Dismiss (light: 'noted, move on')"
                          >
                            ✕
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              )
            })}
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
  pending,
  onAddFiles,
  onRemovePending,
  voiceState,
  voiceError,
  voiceLastHeard,
  onEnterVoice,
  onExitVoice,
}: {
  messages: ChatMessage[]
  input: string
  setInput: (v: string) => void
  sending: boolean
  onSend: () => void
  onClear: () => void
  onRefresh: () => void
  pending: PendingAttachment[]
  onAddFiles: (files: FileList | File[]) => void
  onRemovePending: (localId: string) => void
  voiceState: VoiceState
  voiceError: string | null
  voiceLastHeard: string
  onEnterVoice: () => void
  onExitVoice: () => void
}) {
  const voiceActive = voiceState !== 'off'
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const messagesEndRef = useRef<HTMLDivElement | null>(null)
  const messagesScrollRef = useRef<HTMLDivElement | null>(null)
  const [dragOver, setDragOver] = useState<boolean>(false)
  const [stickToBottom, setStickToBottom] = useState<boolean>(true)

  // Auto-scroll-to-bottom when new messages arrive — but only if the
  // user is already at (or near) the bottom. Reading older messages
  // shouldn't yank them down on every fetch tick.
  useEffect(() => {
    if (!stickToBottom) return
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages.length, sending, stickToBottom])

  // Track scroll position so stickToBottom flips off when the user
  // scrolls up to read history, and back on when they return to the bottom.
  const handleScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
    setStickToBottom(distanceFromBottom < 80)
  }

  const sendDisabled = sending
    || (!input.trim() && pending.filter((p) => !p.error).length === 0)
    || pending.some((p) => p.status === 'uploading')

  const droppableHandlers = {
    onDragEnter: (e: React.DragEvent) => {
      if (Array.from(e.dataTransfer.types).includes('Files')) {
        e.preventDefault(); setDragOver(true)
      }
    },
    onDragOver: (e: React.DragEvent) => {
      if (Array.from(e.dataTransfer.types).includes('Files')) {
        e.preventDefault(); e.dataTransfer.dropEffect = 'copy'
      }
    },
    onDragLeave: (e: React.DragEvent) => {
      // Only clear when leaving the wrapping element itself, not a child
      if (e.currentTarget === e.target) setDragOver(false)
    },
    onDrop: (e: React.DragEvent) => {
      e.preventDefault(); setDragOver(false)
      const files = e.dataTransfer.files
      if (files && files.length) onAddFiles(files)
    },
  }

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

      <div
        ref={messagesScrollRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto px-6 py-4"
      >
        <div className="max-w-3xl mx-auto space-y-4">
          {messages.length === 0 ? (
            <div className="text-sm text-text-muted text-center py-12">
              Talk to the overseer. It has access to your working memory,
              recent gists, themes, and the institutional notes left by the
              first instance. Ask anything — what you've been working on,
              what you might be forgetting, what it thinks of a pattern it
              has noticed.
              <div className="mt-4 text-xs">
                Drop a file or click the paperclip to attach images, code,
                docs, or PDFs.
              </div>
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
          <div ref={messagesEndRef} />
        </div>
      </div>

      <div
        className={`border-t border-border px-6 py-3 bg-surface-secondary relative ${
          dragOver ? 'ring-2 ring-accent ring-inset' : ''
        }`}
        {...droppableHandlers}
      >
        {dragOver && (
          <div className="absolute inset-0 flex items-center justify-center bg-accent/10 pointer-events-none z-10">
            <div className="text-sm font-medium text-accent">
              Drop to attach (max {CHAT_MAX_FILES}, {formatBytes(CHAT_MAX_FILE_BYTES)} each)
            </div>
          </div>
        )}

        {pending.length > 0 && (
          <div className="max-w-3xl mx-auto mb-2 flex flex-wrap gap-2">
            {pending.map((p) => (
              <PendingAttachmentChip
                key={p.localId}
                p={p}
                onRemove={() => onRemovePending(p.localId)}
              />
            ))}
          </div>
        )}

        {/* Slice 14: voice mode status banner */}
        {voiceActive && (
          <div className="max-w-3xl mx-auto mb-2">
            <VoiceModeBanner
              state={voiceState}
              lastHeard={voiceLastHeard}
              onExit={onExitVoice}
            />
          </div>
        )}
        {voiceError && (
          <div className="max-w-3xl mx-auto mb-2 text-xs text-red-400">
            {voiceError}
          </div>
        )}

        <div className="max-w-3xl mx-auto flex gap-2 items-end">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept={[...CHAT_ALLOWED_EXTS].join(',')}
            className="hidden"
            onChange={(e) => {
              if (e.target.files && e.target.files.length) {
                onAddFiles(e.target.files)
              }
              // Reset so the same filename can be picked again later
              e.target.value = ''
            }}
          />
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={sending || voiceActive || pending.length >= CHAT_MAX_FILES}
            title="Attach files (max 10, 5MB each)"
            className="h-9 w-9 shrink-0 rounded-md flex items-center justify-center bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-muted hover:text-text-primary cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
            aria-label="Attach files"
          >
            {/* Paperclip icon, simple inline SVG so we don't pull a deps */}
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" strokeWidth="2" strokeLinecap="round"
                 strokeLinejoin="round">
              <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>
            </svg>
          </button>
          {/* Slice 14: voice mode toggle — one press enters continuous
              voice conversation, press again to exit to text. */}
          <button
            type="button"
            onClick={() => (voiceActive ? onExitVoice() : onEnterVoice())}
            disabled={sending}
            title={voiceActive
              ? 'Exit voice mode'
              : 'Voice mode — talk to the overseer'}
            className={`h-9 w-9 shrink-0 rounded-md flex items-center justify-center cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed ${
              voiceActive
                ? 'bg-accent text-white'
                : 'bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-muted hover:text-text-primary'
            }`}
            aria-label={voiceActive ? 'Exit voice mode' : 'Enter voice mode'}
          >
            {voiceActive ? (
              /* Stop square */
              <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                <rect x="5" y="5" width="14" height="14" rx="2" />
              </svg>
            ) : (
              /* Microphone */
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
                   stroke="currentColor" strokeWidth="2" strokeLinecap="round"
                   strokeLinejoin="round">
                <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
                <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
                <line x1="12" y1="19" x2="12" y2="23"/>
                <line x1="8" y1="23" x2="16" y2="23"/>
              </svg>
            )}
          </button>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                onSend()
              }
            }}
            placeholder={
              voiceActive
                ? 'Voice mode active — speak, or press the stop button to return to text.'
                : pending.length
                ? 'Add a question, or send the files alone…'
                : 'Type a message… (/ for commands, Enter to send, Shift+Enter for newline)'
            }
            disabled={sending || voiceActive}
            rows={2}
            className="flex-1 rounded-md border border-border bg-surface-tertiary px-3 py-2 text-sm text-text-primary placeholder:text-text-muted resize-none focus:outline-none focus:border-accent disabled:opacity-50"
          />
          <button
            onClick={onSend}
            disabled={sendDisabled}
            className="px-4 py-2 rounded-md text-sm font-medium bg-accent hover:bg-accent-hover text-white cursor-pointer disabled:opacity-50 self-end"
          >
            {sending ? 'Sending…' : 'Send'}
          </button>
        </div>
      </div>
    </div>
  )
}

// Slice 14: voice-mode status banner. Shows the live state of the
// conversation loop (listening / transcribing / thinking / speaking)
// plus what was last heard, with a stop control.
function VoiceModeBanner({
  state,
  lastHeard,
  onExit,
}: {
  state: VoiceState
  lastHeard: string
  onExit: () => void
}) {
  const label: Record<VoiceState, string> = {
    off: '',
    listening: 'Listening…',
    transcribing: 'Transcribing…',
    thinking: 'Overseer is thinking…',
    speaking: 'Speaking…',
  }
  const dotColor: Record<VoiceState, string> = {
    off: '#64748b',
    listening: '#10b981',
    transcribing: '#06b6d4',
    thinking: '#f59e0b',
    speaking: '#7c5cff',
  }
  return (
    <div className="flex items-center gap-3 rounded-md bg-surface-tertiary px-3 py-2">
      <span
        className="w-2.5 h-2.5 rounded-full shrink-0"
        style={{
          background: dotColor[state],
          animation: state === 'listening' || state === 'speaking'
            ? 'pulse 1.4s ease-in-out infinite'
            : undefined,
        }}
      />
      <div className="flex-1 min-w-0">
        <div className="text-xs font-medium text-text-primary">
          Voice mode · {label[state]}
        </div>
        {lastHeard && (
          <div className="text-[11px] text-text-muted truncate">
            heard: "{lastHeard}"
          </div>
        )}
      </div>
      <button
        onClick={onExit}
        className="text-xs px-2 py-1 rounded bg-surface-secondary hover:bg-red-500/20 text-text-secondary hover:text-red-400 cursor-pointer shrink-0"
      >
        Stop
      </button>
    </div>
  )
}

// Slice 9.6 CP1 (2026-05-19): renders overseer-attached custom action
// buttons on a notification. Four interaction models depending on
// action.kind:
//   - 'free_text'         → click expands a textarea; submit logs reply
//   - 'yes_no'            → two button cluster: Yes / No (auto-payload)
//   - 'dispatch_sibling'  → single button (overseer reads + creates the
//                            sibling task on next tick from the response
//                            payload)
//   - all other kinds     → click immediately POSTs the action's payload
//                            verbatim ('archive_project', 'mark_dormant',
//                            etc. — overseer reads the response next tick
//                            and acts via its write tools)
function NotificationCustomActions({
  notification,
  onRespond,
}: {
  notification: NotificationRow
  onRespond: (
    notification_id: number,
    action_kind: string,
    action_label: string,
    response_payload?: Record<string, any>,
    also_archive?: boolean,
  ) => void
}) {
  const [freeTextOpen, setFreeTextOpen] = useState<string | null>(null)
  const [freeTextValue, setFreeTextValue] = useState('')
  const actions = notification.actions || []
  if (actions.length === 0) return null

  const click = (a: NotificationAction) => {
    if (a.kind === 'free_text') {
      setFreeTextOpen(a.label)
      setFreeTextValue('')
      return
    }
    if (a.kind === 'yes_no') {
      // The action's payload.value should carry 'yes' or 'no'; if not,
      // derive from the label as a best-effort fallback.
      const value =
        a.payload?.value ??
        (a.label.toLowerCase().includes('yes') ? 'yes' : 'no')
      onRespond(notification.id, a.kind, a.label, { ...a.payload, value }, true)
      return
    }
    // dispatch_sibling + predefined CRUD + custom — fire immediately
    onRespond(
      notification.id,
      a.kind,
      a.label,
      a.payload || {},
      true,  // auto-archive — Tory has handled the notification
    )
  }

  const submitFreeText = (label: string) => {
    if (!freeTextValue.trim()) return
    onRespond(
      notification.id,
      'free_text',
      label,
      { value: freeTextValue.trim() },
      true,
    )
    setFreeTextOpen(null)
    setFreeTextValue('')
  }

  return (
    <div className="mt-2.5 space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        {actions.map((a, i) => {
          // Color cue per kind so Tory eyes-down can tell them apart.
          const cls =
            a.kind === 'yes_no'
              ? 'bg-accent/20 hover:bg-accent/30 text-accent-hover border-accent/40'
              : a.kind === 'free_text'
                ? 'bg-surface-tertiary hover:bg-surface-tertiary/80 text-text-primary border-border'
                : a.kind === 'dispatch_sibling'
                  ? 'bg-purple-500/15 hover:bg-purple-500/25 text-purple-300 border-purple-500/30'
                  : 'bg-amber-500/15 hover:bg-amber-500/25 text-amber-300 border-amber-500/30'
          return (
            <button
              key={i}
              onClick={() => click(a)}
              className={`px-2.5 py-1 rounded-md text-[11px] font-medium border cursor-pointer ${cls}`}
              title={`kind: ${a.kind}${a.payload ? ' · payload: ' + JSON.stringify(a.payload).slice(0, 80) : ''}`}
            >
              {a.label}
            </button>
          )
        })}
      </div>
      {freeTextOpen !== null && (
        <div className="rounded-md border border-border bg-surface-tertiary/40 p-2">
          <div className="text-[10px] text-text-muted mb-1">
            Reply to overseer — your text is logged and surfaced to them
            on their next tick.
          </div>
          <textarea
            value={freeTextValue}
            onChange={(e) => setFreeTextValue(e.target.value)}
            rows={3}
            autoFocus
            placeholder="Type your reply…"
            className="w-full text-xs rounded border border-border bg-surface-secondary p-2 text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent resize-none"
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                e.preventDefault()
                submitFreeText(freeTextOpen)
              }
            }}
          />
          <div className="mt-1.5 flex items-center gap-2 justify-end">
            <button
              onClick={() => {
                setFreeTextOpen(null)
                setFreeTextValue('')
              }}
              className="px-2.5 py-1 rounded-md text-[11px] font-medium text-text-muted hover:text-text-primary cursor-pointer"
            >
              Cancel
            </button>
            <button
              onClick={() => submitFreeText(freeTextOpen)}
              disabled={!freeTextValue.trim()}
              className="px-2.5 py-1 rounded-md text-[11px] font-medium bg-accent/20 hover:bg-accent/30 text-accent-hover border border-accent/40 cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Send reply (Ctrl+Enter)
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

function PendingAttachmentChip({
  p, onRemove,
}: { p: PendingAttachment; onRemove: () => void }) {
  const kind = classifyKind(p.filename, p.mime_type)
  const isError = p.status === 'error' || !!p.error
  const isUploading = p.status === 'uploading'

  return (
    <div
      className={`flex items-center gap-2 max-w-xs rounded-md border px-2 py-1 text-xs ${
        isError
          ? 'border-red-500/40 bg-red-500/10 text-red-300'
          : 'border-border bg-surface-tertiary text-text-primary'
      }`}
      title={isError ? p.error : `${p.filename} · ${formatBytes(p.size)}`}
    >
      {kind === 'image' && p.previewUrl ? (
        <img src={p.previewUrl} alt={p.filename}
             className="h-8 w-8 object-cover rounded" />
      ) : (
        <span className="h-8 w-8 shrink-0 rounded flex items-center justify-center text-[10px] uppercase tracking-wide bg-surface-secondary text-text-muted">
          {kind === 'pdf' ? 'PDF' : kind === 'text' ? 'TXT' : 'FILE'}
        </span>
      )}
      <div className="min-w-0 flex-1">
        <div className="truncate font-medium">{p.filename}</div>
        <div className="text-[10px] text-text-muted">
          {isUploading
            ? 'uploading…'
            : isError
              ? (p.error || 'error')
              : p.status === 'ready'
                ? `${formatBytes(p.size)} · ready`
                : formatBytes(p.size)}
        </div>
      </div>
      <button
        onClick={onRemove}
        disabled={isUploading}
        className="text-text-muted hover:text-red-400 cursor-pointer disabled:opacity-50"
        aria-label="Remove attachment"
        title="Remove"
      >
        ×
      </button>
    </div>
  )
}

function ChatBubble({ m }: { m: ChatMessage }) {
  const isUser = m.role === 'user'
  const isSystem = m.role === 'system'
  const attachments = m.attachments || []
  return (
    <div
      className={`flex ${
        isUser ? 'justify-end' : isSystem ? 'justify-center' : 'justify-start'
      }`}
    >
      <div
        className={`max-w-[80%] rounded-lg px-4 py-3 text-sm leading-relaxed ${
          isUser
            ? 'bg-accent/15 text-text-primary border border-accent/30'
            : isSystem
              ? 'bg-surface-tertiary/40 text-text-secondary border border-border/40 max-w-[90%]'
              : 'bg-surface-secondary text-text-primary border border-border'
        }`}
      >
        <div className="text-[10px] uppercase tracking-wide text-text-muted mb-1">
          {isUser ? 'you' : isSystem ? 'system' : 'overseer'}
          {!isUser && !isSystem && m.model && (
            <span className="ml-2 normal-case">
              {m.model} · {m.latency_ms}ms · ${(m.cost_usd ?? 0).toFixed(4)}
            </span>
          )}
        </div>

        {attachments.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-2">
            {attachments.map((a) => (
              <ChatAttachmentBadge key={a.id} a={a} />
            ))}
          </div>
        )}

        {/* Slice 9.5 CP1 (2026-05-19): assistant messages render as
            markdown (GFM = tables, strikethrough, task lists). User
            messages stay plain to avoid surprising formatting if Tory
            types backtick code or # headers conversationally. */}
        {isUser ? (
          <div className="whitespace-pre-wrap">{m.content}</div>
        ) : (
          <div className="chat-markdown text-text-primary">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                p: ({ children }) => (
                  <p className="mb-2 last:mb-0 leading-relaxed">{children}</p>
                ),
                ul: ({ children }) => (
                  <ul className="list-disc pl-5 mb-2 space-y-1">{children}</ul>
                ),
                ol: ({ children }) => (
                  <ol className="list-decimal pl-5 mb-2 space-y-1">{children}</ol>
                ),
                li: ({ children }) => <li className="leading-relaxed">{children}</li>,
                h1: ({ children }) => (
                  <h1 className="text-base font-semibold mb-2 mt-3 first:mt-0">{children}</h1>
                ),
                h2: ({ children }) => (
                  <h2 className="text-sm font-semibold mb-2 mt-3 first:mt-0">{children}</h2>
                ),
                h3: ({ children }) => (
                  <h3 className="text-sm font-medium mb-1 mt-2 first:mt-0">{children}</h3>
                ),
                code: ({ className, children, ...rest }) => {
                  const isInline = !className
                  return isInline ? (
                    <code className="rounded bg-surface-tertiary px-1 py-0.5 text-xs font-mono">
                      {children}
                    </code>
                  ) : (
                    <code className={className} {...rest}>
                      {children}
                    </code>
                  )
                },
                pre: ({ children }) => (
                  <pre className="rounded bg-surface-tertiary border border-border px-3 py-2 my-2 text-xs font-mono overflow-x-auto">
                    {children}
                  </pre>
                ),
                blockquote: ({ children }) => (
                  <blockquote className="border-l-2 border-accent/40 pl-3 my-2 text-text-secondary italic">
                    {children}
                  </blockquote>
                ),
                a: ({ href, children }) => (
                  <a
                    href={href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-accent hover:underline"
                  >
                    {children}
                  </a>
                ),
                strong: ({ children }) => (
                  <strong className="font-semibold text-text-primary">{children}</strong>
                ),
                table: ({ children }) => (
                  <div className="overflow-x-auto my-2">
                    <table className="text-xs border-collapse">{children}</table>
                  </div>
                ),
                th: ({ children }) => (
                  <th className="border border-border px-2 py-1 bg-surface-tertiary font-medium">
                    {children}
                  </th>
                ),
                td: ({ children }) => (
                  <td className="border border-border px-2 py-1">{children}</td>
                ),
              }}
            >
              {m.content}
            </ReactMarkdown>
          </div>
        )}

        {/* Slice 9.5 CP1: tool-call audit. Shows which tools overseer
            invoked and in what order. Multi-iter loops collapse by
            iter number so a 3-iteration tool-use exchange reads
            cleanly. Tory's directive: "We will want to show the tool
            use of overseer as well in the chat screen." */}
        {!isUser && !isSystem && m.tool_calls && m.tool_calls.length > 0 && (
          <ChatToolCallList calls={m.tool_calls} iterations={m.tool_iterations} />
        )}
      </div>
    </div>
  )
}

// Slice 9.5 CP1: tool-call audit display under assistant messages.
// Compact by default (one row per call); each row expands on click
// to show the args + result size. Keeps the chat readable while
// preserving the audit trail.
function ChatToolCallList({
  calls,
  iterations,
}: {
  calls: ChatToolCallSummary[]
  iterations?: number
}) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  if (!calls.length) return null
  const toggle = (i: number) => {
    setExpanded((s) => {
      const next = new Set(s)
      if (next.has(i)) next.delete(i)
      else next.add(i)
      return next
    })
  }
  return (
    <div className="mt-3 pt-2 border-t border-border/40">
      <div className="text-[10px] uppercase tracking-wide text-text-muted mb-1">
        tool calls ({calls.length}
        {iterations && iterations !== calls.length ? ` across ${iterations} iter` : ''})
      </div>
      <div className="space-y-1">
        {calls.map((c, i) => {
          const isOpen = expanded.has(i)
          return (
            <div
              key={i}
              className="rounded bg-surface-tertiary/50 border border-border/40 text-xs"
            >
              <button
                type="button"
                onClick={() => toggle(i)}
                className="w-full px-2 py-1 flex items-center gap-2 text-left hover:bg-surface-tertiary/80 cursor-pointer"
              >
                <span className="text-[10px] text-text-muted font-mono">#{c.iter}</span>
                <span className="font-mono text-accent flex-1 truncate">{c.name}</span>
                <span className="text-[10px] text-text-muted whitespace-nowrap">
                  {c.result_chars >= 1024
                    ? `${(c.result_chars / 1024).toFixed(1)}k`
                    : c.result_chars}{' '}
                  chars
                </span>
                <span className="text-text-muted text-[10px]">{isOpen ? '▾' : '▸'}</span>
              </button>
              {isOpen && (
                <div className="px-2 pb-2 pt-1 border-t border-border/40">
                  <div className="text-[10px] text-text-muted mb-0.5">args:</div>
                  <pre className="text-[10px] font-mono whitespace-pre-wrap break-all text-text-secondary">
                    {JSON.stringify(c.args, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

function ChatAttachmentBadge({ a }: { a: ChatStoredAttachment }) {
  // Slice 8: render an image thumbnail when possible, otherwise a
  // labelled chip. The thumbnail uses /api/pi/files/<category>/<name>
  // when the file lives under uploads/, falling back to a generic
  // chip if that route isn't available. For now we punt on the image
  // src and render a text chip everywhere — the chat history still
  // shows what was attached. Slice D will add real thumbs.
  const label = a.kind === 'image' ? 'IMG'
    : a.kind === 'pdf' ? 'PDF'
    : a.kind === 'text' ? 'TXT' : 'FILE'
  return (
    <div
      className="flex items-center gap-2 max-w-xs rounded-md border border-border bg-surface-tertiary/50 px-2 py-1 text-xs"
      title={`${a.filename} · ${formatBytes(a.size_bytes)} · ${a.mime_type || a.kind}`}
    >
      <span className="h-7 w-7 shrink-0 rounded flex items-center justify-center text-[10px] uppercase tracking-wide bg-surface-secondary text-text-muted">
        {label}
      </span>
      <div className="min-w-0 flex-1">
        <div className="truncate font-medium">{a.filename}</div>
        <div className="text-[10px] text-text-muted">
          {formatBytes(a.size_bytes)}
        </div>
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

// Color-coded pill for the import row's source.  Compact (no text on
// the pill itself — the abbreviation lives in the title attribute) so
// it sits inline with the project name without crowding the row.
//   claude-code → orange (Anthropic)
//   chatgpt     → green  (OpenAI)
//   other       → gray   (forward-compatible for future sources)
function SourceBadge({ source }: { source: string }) {
  const cfg: Record<string, { label: string; cls: string; full: string }> = {
    'claude-code': {
      label: 'CC',
      cls: 'bg-orange-500/15 text-orange-400 border-orange-500/30',
      full: 'Claude Code',
    },
    'chatgpt': {
      label: 'GPT',
      cls: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
      full: 'ChatGPT',
    },
  }
  const c = cfg[source] || {
    label: (source || '?').slice(0, 3).toUpperCase(),
    cls: 'bg-text-muted/15 text-text-muted border-border',
    full: source || 'unknown source',
  }
  return (
    <span
      title={c.full}
      className={`inline-flex shrink-0 items-center justify-center text-[9px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded border ${c.cls}`}
    >
      {c.label}
    </span>
  )
}

// ── Slice 3i CP1: Projects classification table ─────────────

function ProjectsPanel({
  projects,
  filter,
  setFilter,
  onSetClass,
  onClassifyNow,
  onRefresh,
  busy,
}: {
  projects: ProjectClassRow[]
  filter: string
  setFilter: (f: string) => void
  onSetClass: (
    project: string,
    treat_as: 'auto' | 'human' | 'automation' | 'ignore',
  ) => void
  onClassifyNow: () => void
  onRefresh: () => void
  busy: string
}) {
  const norm = filter.trim().toLowerCase()
  const filtered = norm
    ? projects.filter((p) =>
        (p.project || '(unclassified)').toLowerCase().includes(norm))
    : projects

  // Counts per classification — useful overview.
  const counts = projects.reduce<Record<string, number>>((acc, p) => {
    const k = p.treat_as || 'auto'
    acc[k] = (acc[k] || 0) + 1
    return acc
  }, {})

  const classBadge = (treat_as: string) => {
    if (treat_as === 'human') return 'bg-success/20 text-success'
    if (treat_as === 'automation') return 'bg-amber-500/20 text-amber-400'
    if (treat_as === 'ignore') return 'bg-text-muted/20 text-text-muted'
    return 'bg-accent/20 text-accent-hover'   // auto
  }

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-6xl mx-auto space-y-4">
        <div className="flex items-baseline justify-between">
          <div>
            <h3 className="text-base font-semibold text-text-primary">
              Project classification
            </h3>
            <p className="text-xs text-text-muted mt-1">
              The auto-classifier tags each project as <em>human</em>,{' '}
              <em>automation</em>, or <em>auto</em> (unclassified) based on
              session count + median duration. Insight scans, rollups, and
              other loops respect this. Manual overrides are sticky — the
              auto-classifier won't touch them.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={onRefresh}
              disabled={!!busy}
              className="px-3 py-1.5 rounded-md text-xs font-medium bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-primary cursor-pointer disabled:opacity-50"
            >
              Refresh
            </button>
            <button
              onClick={onClassifyNow}
              disabled={!!busy}
              className="px-3 py-1.5 rounded-md text-xs font-medium bg-accent hover:bg-accent-hover text-white cursor-pointer disabled:opacity-50"
            >
              {busy ? busy : 'Re-classify all'}
            </button>
          </div>
        </div>

        {/* Class summary pills */}
        <div className="flex items-center gap-2 text-xs text-text-secondary">
          <span className="text-text-muted uppercase tracking-wide text-[10px]">
            Distribution:
          </span>
          {(['human', 'automation', 'auto', 'ignore'] as const).map((c) => (
            <span
              key={c}
              className={`px-2 py-0.5 rounded text-[10px] font-medium uppercase ${classBadge(c)}`}
            >
              {c} ({counts[c] || 0})
            </span>
          ))}
        </div>

        {/* Filter */}
        <input
          type="text"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter projects…"
          className="w-full px-3 py-1.5 bg-surface-tertiary text-text-primary text-xs rounded border border-border focus:outline-none focus:border-accent"
        />

        {/* Table */}
        {filtered.length === 0 ? (
          <div className="text-sm text-text-muted italic py-6 text-center">
            {projects.length === 0
              ? 'No projects yet — import some Claude sessions on the Overview tab.'
              : 'No projects match that filter.'}
          </div>
        ) : (
          <div className="border border-border rounded-lg overflow-x-auto">
            <table className="w-full text-xs min-w-[720px]">
              <thead className="bg-surface-secondary text-text-muted uppercase text-[10px]">
                <tr>
                  <th className="text-left px-3 py-2">Project</th>
                  <th className="text-right px-3 py-2">Sessions</th>
                  <th className="text-right px-3 py-2">Median min</th>
                  <th className="text-left px-3 py-2">Last seen</th>
                  <th className="text-left px-3 py-2">Class</th>
                  <th className="text-left px-3 py-2">Override → set</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((p, idx) => {
                  const display = p.project || '(unclassified)'
                  return (
                    <tr
                      key={p.project || `__unclassified_${idx}`}
                      className="border-t border-border hover:bg-surface-secondary/40"
                    >
                      <td className="px-3 py-2 text-text-primary font-medium truncate max-w-xs">
                        {display}
                      </td>
                      <td className="px-3 py-2 text-text-secondary text-right">
                        {p.session_count}
                      </td>
                      <td className="px-3 py-2 text-text-muted text-right">
                        {p.avg_duration_minutes?.toFixed(1) ?? '—'}
                      </td>
                      <td className="px-3 py-2 text-text-muted">
                        {p.last_seen?.slice(0, 10) || '—'}
                      </td>
                      <td className="px-3 py-2">
                        <span
                          className={`px-1.5 py-0.5 rounded text-[10px] font-medium uppercase ${classBadge(p.treat_as)}`}
                        >
                          {p.treat_as}
                        </span>
                        {p.manual_override && (
                          <span
                            className="ml-1 text-[10px] text-text-muted"
                            title={`Set ${fmtTime((p as any).local_classified_at, p.classified_at) || ''} (${p.classified_reason || 'manual'})`}
                          >
                            🔒
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex items-center gap-1 flex-wrap">
                          {(['human', 'automation', 'ignore', 'auto'] as const).map((c) => (
                            <button
                              key={c}
                              disabled={!!busy || p.treat_as === c}
                              onClick={() => onSetClass(p.project, c)}
                              className={`px-1.5 py-0.5 rounded text-[10px] uppercase font-medium cursor-pointer disabled:opacity-40 disabled:cursor-default ${
                                p.treat_as === c
                                  ? classBadge(c)
                                  : 'bg-surface-tertiary text-text-muted hover:text-text-primary'
                              }`}
                              title={c === 'auto'
                                ? 'Clear manual override; let the classifier decide'
                                : `Force this project to ${c}`}
                            >
                              {c}
                            </button>
                          ))}
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Slice 3h: Insights review queue ─────────────────────────

function InsightsPanel({
  interpretations,
  counts,
  scans,
  statusFilter,
  setStatusFilter,
  scanProject,
  setScanProject,
  scanDays,
  setScanDays,
  onScanNow,
  onDistillCorrections,
  onDecide,
  editingId,
  setEditing,
  editTitle,
  editBody,
  setEditTitle,
  setEditBody,
  onTokenClick,
  busy,
}: {
  interpretations: PendingInterpretation[]
  counts?: InsightPendingResp['counts']
  scans: InsightScanRow[]
  statusFilter: string
  setStatusFilter: (s: string) => void
  scanProject: string
  setScanProject: (p: string) => void
  scanDays: number
  setScanDays: (d: number) => void
  onScanNow: () => void
  onDistillCorrections: () => void
  onDecide: (
    id: number,
    decision: 'confirm' | 'reject' | 'edit-and-confirm',
    overrides?: { edit_title?: string; edit_body?: string; review_note?: string },
  ) => void
  editingId: number | null
  setEditing: (id: number | null, title: string, body: string) => void
  editTitle: string
  editBody: string
  setEditTitle: (t: string) => void
  setEditBody: (b: string) => void
  onTokenClick: (token: string) => void
  busy: string
}) {
  const kindBadgeClass = (kind: string) => {
    if (kind === 'theme') return 'bg-accent/20 text-accent-hover'
    if (kind === 'pattern') return 'bg-amber-500/20 text-amber-400'
    if (kind === 'drift') return 'bg-success/20 text-success'
    if (kind === 'blindspot') return 'bg-red-500/20 text-red-400'
    return 'bg-surface-tertiary text-text-muted'
  }

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-5xl mx-auto space-y-6">
        <div>
          <h3 className="text-base font-semibold text-text-primary">
            Proposed insights (Sonnet → human review)
          </h3>
          <p className="text-xs text-text-muted mt-1">
            The overseer scans recent gist arcs per project and proposes
            new themes / patterns / drift it sees emerging. Nothing
            applies until you confirm. Reject the noise. Edit the title
            or body if a candidate is real but the framing is off.
          </p>
        </div>

        {/* Scan trigger */}
        <div className="bg-surface-secondary border border-border rounded-lg p-4">
          <div className="text-xs uppercase tracking-wide text-text-muted mb-2">
            Trigger a scan
          </div>
          <div className="flex items-baseline gap-2 flex-wrap">
            <input
              type="text"
              value={scanProject}
              onChange={(e) => setScanProject(e.target.value)}
              placeholder="project tag (e.g. UFOSINT)"
              className="px-3 py-1.5 bg-surface-tertiary text-text-primary text-xs rounded-md border border-border focus:outline-none focus:border-accent w-56"
            />
            <input
              type="number"
              value={scanDays}
              onChange={(e) => setScanDays(parseInt(e.target.value) || 7)}
              min={1}
              max={90}
              className="px-3 py-1.5 bg-surface-tertiary text-text-primary text-xs rounded-md border border-border focus:outline-none focus:border-accent w-16"
            />
            <span className="text-xs text-text-muted">days</span>
            <button
              onClick={onScanNow}
              disabled={!!busy || !scanProject.trim()}
              className="ml-2 px-3 py-1.5 rounded-md text-xs font-medium bg-accent hover:bg-accent-hover text-white cursor-pointer disabled:opacity-50"
            >
              {busy ? busy : 'Scan now'}
            </button>
          </div>
          <p className="text-[10px] text-text-muted mt-2 italic">
            Single Sonnet call; cost capped at $0.05/scan. Cheap projects
            run for fractions of a cent. The auto-loop also scans up to
            2 active+human projects per tick (24h cadence per project).
          </p>
        </div>

        {/* 3i CP2: distill corrections → blindspot proposals */}
        <div className="bg-surface-secondary border border-border rounded-lg p-4">
          <div className="text-xs uppercase tracking-wide text-text-muted mb-2">
            Distill corrections → blindspots
          </div>
          <p className="text-[11px] text-text-secondary mb-3 leading-relaxed">
            User corrections (from chat, dialectic resolutions, or manual
            log) are clustered by Sonnet into blindspot candidates that
            land in this same review queue with kind=blindspot. The
            auto-loop runs this once per 24h if there are at least 3
            uncondidated corrections.
          </p>
          <button
            onClick={onDistillCorrections}
            disabled={!!busy}
            className="px-3 py-1.5 rounded-md text-xs font-medium bg-accent hover:bg-accent-hover text-white cursor-pointer disabled:opacity-50"
          >
            {busy ? busy : 'Distill now'}
          </button>
        </div>

        {/* Recent scans (auto-loop visibility) */}
        {scans.length > 0 && (
          <details className="bg-surface-secondary border border-border rounded-lg p-4">
            <summary className="cursor-pointer text-xs uppercase tracking-wide text-text-muted">
              Recent scans ({scans.length})
            </summary>
            <ul className="mt-3 space-y-1 text-[11px]">
              {scans.map((s) => {
                const okColor = s.ok
                  ? (s.candidates_proposed > 0
                      ? 'text-success'
                      : 'text-text-muted')
                  : 'text-red-400'
                return (
                  <li
                    key={s.id}
                    className="grid grid-cols-[max-content_max-content_1fr_max-content_max-content] gap-x-3 items-baseline"
                  >
                    <span className="text-text-muted text-[10px]">
                      {fmtRelative(s.scanned_at)}
                    </span>
                    <span className="text-[9px] uppercase tracking-wide text-text-muted">
                      {s.triggered_by}
                    </span>
                    <span className="text-text-secondary truncate">
                      {s.project || s.scan_kind}
                    </span>
                    <span className={okColor}>
                      {s.ok
                        ? `${s.candidates_proposed} new` +
                          (s.candidates_deduped > 0
                            ? ` (+${s.candidates_deduped} dup)`
                            : '')
                        : `error: ${(s.error || '').slice(0, 40)}`}
                    </span>
                    <span className="text-text-muted text-[10px]">
                      {s.cost_usd > 0 ? `$${s.cost_usd.toFixed(4)}` : '$0'}
                      {s.error && s.error.includes('insufficient') && ' · skipped'}
                    </span>
                  </li>
                )
              })}
            </ul>
          </details>
        )}

        {/* Status filter pills */}
        <div className="flex items-center gap-1 bg-surface-secondary border border-border rounded-lg p-1 w-fit">
          {(['pending', 'confirmed', 'edited', 'rejected'] as const).map((s) => {
            const n = counts?.[s] ?? 0
            return (
              <button
                key={s}
                onClick={() => setStatusFilter(s)}
                className={`px-3 py-1 rounded-md text-xs font-medium transition-colors cursor-pointer ${
                  statusFilter === s
                    ? 'bg-accent text-white'
                    : 'text-text-secondary hover:text-text-primary'
                }`}
              >
                {s} {n > 0 && <span className="text-[10px] opacity-80">({n})</span>}
              </button>
            )
          })}
        </div>

        {/* Candidate list */}
        {interpretations.length === 0 ? (
          <div className="text-sm text-text-muted italic">
            {statusFilter === 'pending'
              ? 'No pending candidates. Run a scan above to propose some.'
              : `No ${statusFilter} candidates.`}
          </div>
        ) : (
          <ul className="space-y-3">
            {interpretations.map((it) => (
              <li
                key={it.id}
                className="bg-surface-secondary border border-border rounded-lg p-4 space-y-2"
              >
                <div className="flex items-baseline gap-2 flex-wrap">
                  <span className={`text-[10px] uppercase font-bold px-1.5 py-0.5 rounded ${kindBadgeClass(it.kind)}`}>
                    {it.kind}
                  </span>
                  {it.direction && (
                    <span className="text-[10px] uppercase text-text-muted">
                      {it.direction}
                    </span>
                  )}
                  <span className="text-[10px] uppercase text-text-muted">
                    [{it.confidence}]
                  </span>
                  {/* 3h CP2: source-kind badge so chat-sourced
                      candidates are visually distinct from loop scans */}
                  <span
                    className={`text-[9px] uppercase tracking-wide px-1.5 py-0.5 rounded font-mono ${
                      it.source_kind === 'chat-snippet'
                        ? 'bg-accent-hover/20 text-accent-hover'
                        : 'bg-surface-tertiary text-text-muted'
                    }`}
                    title={
                      it.source_kind === 'chat-snippet'
                        ? `From overseer chat reply (msg #${(it as any).source_chat_message_id ?? '?'})`
                        : `From a periodic ${it.source_kind} scan`
                    }
                  >
                    {it.source_kind}
                  </span>
                  {it.source_project && (
                    <span className="text-[10px] uppercase text-text-secondary">
                      project: {it.source_project}
                    </span>
                  )}
                  <span className="text-[10px] text-text-muted ml-auto">
                    proposed {fmtRelative(it.proposed_at)}
                  </span>
                </div>

                {editingId === it.id ? (
                  <div className="space-y-2">
                    <input
                      type="text"
                      value={editTitle}
                      onChange={(e) => setEditTitle(e.target.value)}
                      className="w-full px-3 py-2 bg-surface-tertiary text-text-primary text-sm rounded border border-accent/40 focus:outline-none focus:border-accent"
                    />
                    <textarea
                      value={editBody}
                      onChange={(e) => setEditBody(e.target.value)}
                      rows={4}
                      className="w-full px-3 py-2 bg-surface-tertiary text-text-secondary text-xs rounded border border-accent/40 focus:outline-none focus:border-accent leading-relaxed"
                    />
                  </div>
                ) : (
                  <div>
                    <div className="text-sm text-text-primary font-medium">
                      {it.title}
                    </div>
                    <p className="text-xs text-text-secondary mt-1 leading-relaxed">
                      {it.body}
                    </p>
                  </div>
                )}

                {/* 3i CP2: blindspot-kind structured fields */}
                {it.kind === 'blindspot' && editingId !== it.id && (
                  <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-0.5 text-[11px] mt-2 bg-surface-tertiary/30 p-2 rounded">
                    <dt className="text-text-muted uppercase tracking-wide text-[10px]">
                      model:
                    </dt>
                    <dd className="text-text-primary font-mono">
                      {it.bs_model_pattern || '*'}
                    </dd>
                    <dt className="text-text-muted uppercase tracking-wide text-[10px]">
                      topic:
                    </dt>
                    <dd className="text-text-secondary font-mono">
                      {it.bs_topic_pattern || '(any)'}
                    </dd>
                    <dt className="text-text-muted uppercase tracking-wide text-[10px]">
                      conf adj:
                    </dt>
                    <dd
                      className={`font-medium ${
                        (it.bs_confidence_adjustment ?? 0) > 0
                          ? 'text-amber-400'
                          : (it.bs_confidence_adjustment ?? 0) < 0
                            ? 'text-text-muted'
                            : 'text-text-secondary'
                      }`}
                    >
                      {(it.bs_confidence_adjustment ?? 0) > 0
                        ? `+${it.bs_confidence_adjustment} (treat reported as too high)`
                        : (it.bs_confidence_adjustment ?? 0) < 0
                          ? `${it.bs_confidence_adjustment} (treat reported as too low)`
                          : '0 (no adjustment)'}
                    </dd>
                  </dl>
                )}

                {it.rationale && (
                  <details className="text-[11px] text-text-muted">
                    <summary className="cursor-pointer uppercase tracking-wide">
                      Rationale
                    </summary>
                    <p className="mt-1 leading-relaxed">{it.rationale}</p>
                  </details>
                )}

                {/* Source pointer ids — meaning depends on kind:
                    - blindspot → correction ids (not drillable today)
                    - everything else → gist ids (clickable token chips) */}
                {(() => {
                  let ids: number[] = []
                  try {
                    ids = JSON.parse(it.source_pointer_ids || '[]')
                  } catch {}
                  if (ids.length === 0) return null
                  const isBlindspot = it.kind === 'blindspot'
                  const label = isBlindspot ? 'Corrections:' : 'Source:'
                  return (
                    <div className="flex items-baseline gap-1.5 flex-wrap text-[10px]">
                      <span className="uppercase tracking-wide text-text-muted">
                        {label}
                      </span>
                      {ids.slice(0, 12).map((sid) =>
                        isBlindspot ? (
                          <span
                            key={sid}
                            className="text-[9px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-surface-tertiary text-text-muted font-mono"
                            title="Correction row id (not drillable today)"
                          >
                            c:{sid}
                          </span>
                        ) : (
                          <TokenChip
                            key={sid}
                            token={`g:${sid}`}
                            onClick={onTokenClick}
                          />
                        ),
                      )}
                      {ids.length > 12 && (
                        <span className="text-text-muted">
                          +{ids.length - 12} more
                        </span>
                      )}
                    </div>
                  )
                })()}

                {/* Action row */}
                {it.status === 'pending' ? (
                  <div className="flex items-center gap-2 pt-2 border-t border-border">
                    {editingId === it.id ? (
                      <>
                        <button
                          onClick={() =>
                            onDecide(it.id, 'edit-and-confirm', {
                              edit_title: editTitle,
                              edit_body: editBody,
                            })
                          }
                          disabled={!!busy}
                          className="px-3 py-1.5 rounded-md text-xs font-medium bg-success/80 hover:bg-success text-white cursor-pointer disabled:opacity-50"
                        >
                          Confirm edited
                        </button>
                        <button
                          onClick={() => setEditing(null, '', '')}
                          disabled={!!busy}
                          className="px-3 py-1.5 rounded-md text-xs font-medium bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-primary cursor-pointer disabled:opacity-50"
                        >
                          Cancel
                        </button>
                      </>
                    ) : (
                      <>
                        <button
                          onClick={() => onDecide(it.id, 'confirm')}
                          disabled={!!busy}
                          className="px-3 py-1.5 rounded-md text-xs font-medium bg-accent hover:bg-accent-hover text-white cursor-pointer disabled:opacity-50"
                        >
                          Confirm
                        </button>
                        <button
                          onClick={() => setEditing(it.id, it.title, it.body)}
                          disabled={!!busy}
                          className="px-3 py-1.5 rounded-md text-xs font-medium bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-primary cursor-pointer disabled:opacity-50"
                        >
                          Edit
                        </button>
                        <button
                          onClick={() => onDecide(it.id, 'reject')}
                          disabled={!!busy}
                          className="px-3 py-1.5 rounded-md text-xs font-medium text-text-muted hover:text-red-400 cursor-pointer disabled:opacity-50"
                        >
                          Reject
                        </button>
                      </>
                    )}
                  </div>
                ) : (
                  <div className="text-[10px] text-text-muted pt-2 border-t border-border">
                    {it.status} {it.reviewed_by && `by ${it.reviewed_by}`}
                    {it.applied_table && (
                      <> → landed in <span className="font-mono">{it.applied_table}#{it.applied_id}</span></>
                    )}
                    {it.review_note && <> · "{it.review_note}"</>}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
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

// ── Slice 3g #2: drill-down chip + inline detail card ─────────

function TokenChip({
  token,
  active,
  onClick,
  className,
}: {
  token?: string | null
  active?: boolean
  onClick: (token: string) => void
  className?: string
}) {
  if (!token) return null
  return (
    <button
      onClick={() => onClick(token)}
      className={`text-[9px] uppercase tracking-wide px-1.5 py-0.5 rounded cursor-pointer transition-colors font-mono ${
        active
          ? 'bg-accent text-white'
          : 'bg-surface-tertiary text-text-muted hover:bg-accent/30 hover:text-accent-hover'
      } ${className || ''}`}
      title={`Drill into ${token}`}
    >
      {token}
    </button>
  )
}

function DetailCard({
  token,
  onNavigate,
  onClose,
}: {
  token: string
  onNavigate: (token: string) => void
  onClose: () => void
}) {
  const [resp, setResp] = useState<DetailResp | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string>('')

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError('')
    setResp(null)
    apiFetch<DetailResp>(`/overseer/detail?token=${encodeURIComponent(token)}`)
      .then((r) => {
        if (cancelled) return
        if (r.ok) setResp(r)
        else setError(r.error || 'detail failed')
      })
      .catch((e) => {
        if (!cancelled) setError(String(e))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [token])

  return (
    <div className="bg-surface-secondary border border-accent/40 rounded-lg p-4 mb-4 text-xs">
      <div className="flex items-baseline justify-between mb-2">
        <div className="flex items-baseline gap-2">
          <span className="text-[10px] uppercase tracking-wide text-accent font-mono">
            {token}
          </span>
          {resp?.type && (
            <span className="text-text-muted text-[10px] uppercase">
              · {resp.type}
            </span>
          )}
        </div>
        <button
          onClick={onClose}
          className="text-text-muted hover:text-text-primary text-[10px] uppercase cursor-pointer"
        >
          Close
        </button>
      </div>

      {loading && (
        <div className="text-text-muted italic">Resolving {token}…</div>
      )}
      {error && <div className="text-red-400">Error: {error}</div>}

      {resp && resp.primary && (
        <div className="space-y-3">
          <div>
            <div className="text-text-muted uppercase tracking-wide text-[10px] mb-1">
              Primary
            </div>
            <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-0.5">
              {Object.entries(resp.primary).map(([k, v]) => (
                <div key={k} className="contents">
                  <dt className="text-text-muted font-mono text-[10px]">
                    {k}
                  </dt>
                  <dd className="text-text-primary whitespace-pre-wrap break-words">
                    {v == null
                      ? <span className="text-text-muted italic">null</span>
                      : typeof v === 'object'
                        ? <span className="text-text-muted font-mono text-[10px]">{JSON.stringify(v)}</span>
                        : String(v)}
                  </dd>
                </div>
              ))}
            </dl>
          </div>

          {resp.tags && resp.tags.length > 0 && (
            <div>
              <div className="text-text-muted uppercase tracking-wide text-[10px] mb-1">
                Tags
              </div>
              <div className="flex flex-wrap gap-1">
                {resp.tags.map((t) => (
                  <span
                    key={t}
                    className="text-[10px] px-1.5 py-0.5 rounded bg-surface-tertiary text-text-secondary"
                  >
                    {t}
                  </span>
                ))}
              </div>
            </div>
          )}

          {resp.context && Object.keys(resp.context).length > 0 && (
            <details className="text-text-secondary">
              <summary className="cursor-pointer text-text-muted uppercase tracking-wide text-[10px]">
                Context ({Object.keys(resp.context).length} keys)
              </summary>
              <pre className="mt-1 text-[10px] font-mono whitespace-pre-wrap break-words bg-surface-tertiary/40 p-2 rounded max-h-60 overflow-y-auto">
                {JSON.stringify(resp.context, null, 2)}
              </pre>
            </details>
          )}

          {resp.next_tokens && resp.next_tokens.length > 0 && (
            <div>
              <div className="text-text-muted uppercase tracking-wide text-[10px] mb-1">
                Drill into ({resp.next_tokens.length})
              </div>
              <ul className="space-y-1">
                {resp.next_tokens.map((nt, i) => (
                  <li
                    key={`${nt.token}-${i}`}
                    className="flex items-baseline gap-2"
                  >
                    <TokenChip token={nt.token} onClick={onNavigate} />
                    <span className="text-text-secondary">{nt.label}</span>
                    {nt.kind && (
                      <span className="text-[10px] text-text-muted italic">
                        {nt.kind}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function WorkingMemoryView({
  wm,
  expandedToken,
  onTokenClick,
  onCloseDetail,
}: {
  wm: WorkingMemory
  expandedToken: string | null
  onTokenClick: (token: string) => void
  onCloseDetail: () => void
}) {
  return (
    <div className="space-y-4 text-xs">
      {expandedToken && (
        <DetailCard
          token={expandedToken}
          onNavigate={onTokenClick}
          onClose={onCloseDetail}
        />
      )}
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
                <div className="flex items-baseline gap-2 flex-wrap">
                  <TokenChip
                    token={q.token}
                    active={expandedToken === q.token}
                    onClick={onTokenClick}
                  />
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
                          className="text-[11px] text-text-secondary flex items-baseline gap-1.5 flex-wrap"
                        >
                          <TokenChip
                            token={ev.token}
                            active={expandedToken === ev.token}
                            onClick={onTokenClick}
                          />
                          <span className={contribColor}>
                            ◆ [{ev.contribution}]
                          </span>
                          <span>
                            {body.length > 200
                              ? body.slice(0, 200) + '…'
                              : body}
                          </span>
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
              <li
                key={g.id}
                className="flex items-baseline gap-1.5 flex-wrap"
              >
                <TokenChip
                  token={g.token}
                  active={expandedToken === g.token}
                  onClick={onTokenClick}
                />
                <span>
                  {g.body.length > 200 ? g.body.slice(0, 200) + '…' : g.body}
                </span>
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
              <li key={t.id} className="flex items-baseline gap-1.5">
                <TokenChip
                  token={t.token}
                  active={expandedToken === t.token}
                  onClick={onTokenClick}
                />
                <span>{t.title}</span>
              </li>
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

      {/* Slice 3g: depth — patterns / drift / institutional notes / rollups */}

      {wm.recent_patterns && wm.recent_patterns.length > 0 && (
        <div>
          <div className="text-text-muted uppercase tracking-wide mb-1">
            Recent Patterns ({wm.recent_patterns.length})
          </div>
          <ul className="space-y-1.5">
            {wm.recent_patterns.map((p) => (
              <li key={p.id} className="border-l-2 border-border pl-3">
                <div className="flex items-baseline gap-2 flex-wrap">
                  <TokenChip
                    token={p.token}
                    active={expandedToken === p.token}
                    onClick={onTokenClick}
                  />
                  <span className="text-text-muted text-[10px] uppercase">
                    [{p.confidence || '?'} · {p.occurrences ?? 1}×]
                  </span>
                  <span className="text-text-primary font-medium">
                    {p.name}
                  </span>
                </div>
                {p.body && (
                  <p className="text-[11px] text-text-secondary mt-0.5">
                    {p.body}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {wm.recent_drift && wm.recent_drift.length > 0 && (
        <div>
          <div className="text-text-muted uppercase tracking-wide mb-1">
            Recent Drift ({wm.recent_drift.length})
            <span className="ml-2 normal-case text-[10px] italic">
              (started / stopped / shifted — what's changing)
            </span>
          </div>
          <ul className="space-y-1">
            {wm.recent_drift.map((d) => {
              const dirColor =
                d.direction === 'started'
                  ? 'text-success'
                  : d.direction === 'stopped'
                    ? 'text-amber-400'
                    : d.direction === 'shifted'
                      ? 'text-accent-hover'
                      : 'text-text-muted'
              return (
                <li
                  key={d.id}
                  className="text-[11px] text-text-secondary flex items-baseline gap-2 flex-wrap"
                >
                  <TokenChip
                    token={d.token}
                    active={expandedToken === d.token}
                    onClick={onTokenClick}
                  />
                  <span className={`text-[10px] uppercase ${dirColor}`}>
                    {d.direction || '—'}
                  </span>
                  <span>{d.body}</span>
                </li>
              )
            })}
          </ul>
        </div>
      )}

      {wm.recent_future_notes && wm.recent_future_notes.length > 0 && (
        <div>
          <div className="text-text-muted uppercase tracking-wide mb-1">
            Notes to Future Overseer ({wm.recent_future_notes.length}
            {wm.future_overseer_notes_count &&
              wm.future_overseer_notes_count > wm.recent_future_notes.length &&
              ` of ${wm.future_overseer_notes_count}`}
            )
            <span className="ml-2 normal-case text-[10px] italic">
              (institutional memory — what prior instances laid down)
            </span>
          </div>
          <ul className="space-y-1.5">
            {wm.recent_future_notes.map((n) => (
              <li key={n.id} className="border-l-2 border-border pl-3">
                <div className="flex items-baseline gap-2 flex-wrap">
                  <TokenChip
                    token={n.token}
                    active={expandedToken === n.token}
                    onClick={onTokenClick}
                  />
                  <span className="text-[10px] text-text-muted uppercase">
                    {n.instance_id}
                    {n.written_at && (
                      <span className="ml-2 normal-case" title={fmtTime((n as any).local_written_at, n.written_at)}>
                        {fmtTime((n as any).local_written_at, n.written_at)}
                      </span>
                    )}
                  </span>
                </div>
                <p className="text-[11px] text-text-secondary mt-0.5">
                  {n.body}
                </p>
              </li>
            ))}
          </ul>
        </div>
      )}

      {wm.recent_rollups && wm.recent_rollups.length > 0 && (
        <div>
          <div className="text-text-muted uppercase tracking-wide mb-1">
            Recent Rollups ({wm.recent_rollups.length})
            <span className="ml-2 normal-case text-[10px] italic">
              (per-project per-day automation digest)
            </span>
          </div>
          <ul className="space-y-1.5">
            {wm.recent_rollups.map((r) => (
              <li
                key={`${r.project}-${r.rollup_date}`}
                className="border-l-2 border-border pl-3"
              >
                <div className="flex items-baseline gap-2 text-[10px] text-text-muted uppercase flex-wrap">
                  <TokenChip
                    token={r.token}
                    active={expandedToken === r.token}
                    onClick={onTokenClick}
                  />
                  <span className="text-text-primary normal-case font-medium">
                    {r.project}
                  </span>
                  <span>{r.rollup_date}</span>
                  {r.session_count != null && (
                    <span>· {r.session_count} sess</span>
                  )}
                  {r.median_minutes != null && (
                    <span>· {r.median_minutes}min median</span>
                  )}
                </div>
                <p className="text-[11px] text-text-secondary mt-0.5">
                  {r.summary}
                </p>
              </li>
            ))}
          </ul>
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
