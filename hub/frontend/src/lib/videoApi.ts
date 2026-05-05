/**
 * Typed client for the cortex-vision plugin's HTTP API.
 *
 * Everything is proxied through the cortex-desktop Hub at
 * /api/video/* (the proxy lives in hub/backend/routers/video.py).
 * Shapes mirror cortex-vision/docs/DATA_MODEL.md verbatim — keep
 * this file synced when the upstream contract evolves.
 */
import { apiFetch } from './api'

export type VideoMode = 'live' | 'file' | 'journal'

export type SessionStatus =
  | 'queued'
  | 'capturing'
  | 'processing'
  | 'describing'
  | 'narrating'
  | 'complete'
  | 'error'

export interface SceneEntry {
  index: number
  start_s: number
  end_s: number
  keyframe_paths: string[]
  description: string
  describer_model: string
  spoken_text: string | null
  objects: string[]
  similarity: number
  trigger_method: string
}

export interface TranscriptEntry {
  timestamp: string
  text: string
  duration_s: number
  latency_ms: number
  rms: number
  chunk_index: number
}

export interface VideoSession {
  id: string
  mode: VideoMode
  source: Record<string, unknown>
  status: SessionStatus
  project_id: string | null
  started_at: string
  ended_at: string | null
  duration_s: number | null
  scenes: SceneEntry[]
  narrative: string | null
  transcript: TranscriptEntry[]
  pushed_to_overseer: boolean
  error: string | null
  progress: { current_scene?: number; total_scenes?: number; [key: string]: unknown }
}

/** Returned by POST /api/video/jobs */
export interface CreateJobResponse {
  session_id: string
  status: SessionStatus
  poll_url: string
}

/** Body for POST /api/video/jobs */
export interface CreateJobRequest {
  source: string                                 // URL or local file path
  mode?: VideoMode                               // defaults to "file"
  project_id?: string | null
  push_to_overseer?: boolean
  transcribe_audio?: boolean
  keyframes_per_scene?: number
  describer_model?: string | null
  narrative_model?: string | null
}

/** Lightweight shape returned by GET /api/video/sessions (list view).
 * The hydrated full shape comes from GET /api/video/sessions/{id}. */
export interface SessionListEntry {
  id: string
  mode: VideoMode
  status: SessionStatus
  project_id: string | null
  started_at: string
  ended_at: string | null
  duration_s: number | null
}

// ---------------------------------------------------------------------------
// Calls — all routed via apiFetch -> /api/video/* through the Hub proxy.
// ---------------------------------------------------------------------------

export async function createJob(req: CreateJobRequest): Promise<CreateJobResponse> {
  return apiFetch<CreateJobResponse>('/video/jobs', {
    method: 'POST',
    body: JSON.stringify(req),
  })
}

export async function getSession(sessionId: string): Promise<VideoSession> {
  return apiFetch<VideoSession>(`/video/sessions/${encodeURIComponent(sessionId)}`)
}

export async function listSessions(opts?: {
  limit?: number
  mode?: VideoMode
}): Promise<SessionListEntry[]> {
  const qs = new URLSearchParams()
  if (opts?.limit) qs.set('limit', String(opts.limit))
  if (opts?.mode) qs.set('mode', opts.mode)
  const q = qs.toString()
  return apiFetch<SessionListEntry[]>(`/video/sessions${q ? `?${q}` : ''}`)
}

/** Build a frame URL pointing at the proxy. The proxy streams the JPEG
 * straight from the sidecar; <img src> works directly. */
export function frameUrl(
  sessionId: string,
  sceneIndex: number,
  frameIndex: number,
): string {
  return `/api/video/jobs/${encodeURIComponent(sessionId)}/frame/${sceneIndex}/${frameIndex}`
}

// ---------------------------------------------------------------------------
// Helpers — status taxonomy
// ---------------------------------------------------------------------------

export const TERMINAL_STATUSES: ReadonlySet<SessionStatus> = new Set([
  'complete',
  'error',
])

export function isTerminal(status: SessionStatus): boolean {
  return TERMINAL_STATUSES.has(status)
}

/** Display label + tailwind color for each status. */
export const STATUS_DISPLAY: Record<SessionStatus, { label: string; color: string }> = {
  queued: { label: 'Queued', color: 'bg-text-muted/30 text-text-secondary' },
  capturing: { label: 'Capturing', color: 'bg-amber-500/15 text-amber-400' },
  processing: { label: 'Processing', color: 'bg-amber-500/15 text-amber-400' },
  describing: { label: 'Describing', color: 'bg-accent/15 text-accent-hover' },
  narrating: { label: 'Narrating', color: 'bg-accent/15 text-accent-hover' },
  complete: { label: 'Complete', color: 'bg-success/15 text-success' },
  error: { label: 'Error', color: 'bg-error/15 text-error' },
}
