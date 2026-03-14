import { useCallback, useEffect, useState } from 'react'
import { apiFetch } from '../../lib/api'

interface LearningStatus {
  ok: boolean
  processed_notes: number
  processed_sessions: number
  total_examples: number
  total_cycles: number
  last_sync_at: string | null
  last_cycle: CycleInfo | null
  cycles: CycleInfo[]
}

interface CycleInfo {
  cycle_id: number
  started_at: string
  notes_processed: number
  sessions_processed: number
  activities_processed?: number
  examples_generated: number
  knowledge_summary: string
  model?: string
}

interface KnowledgeResponse {
  ok: boolean
  summaries: { cycle_id: number; date: string; summary: string; examples_generated: number }[]
  total_cycles: number
  total_examples: number
}

export function LearningTab() {
  const [status, setStatus] = useState<LearningStatus | null>(null)
  const [knowledge, setKnowledge] = useState<KnowledgeResponse | null>(null)
  const [isLearning, setIsLearning] = useState(false)
  const [error, setError] = useState('')

  const fetchStatus = useCallback(async () => {
    try {
      const res = await apiFetch<LearningStatus>('/learning/status')
      if (res?.ok) setStatus(res)
    } catch { /* offline */ }
  }, [])

  const fetchKnowledge = useCallback(async () => {
    try {
      const res = await apiFetch<KnowledgeResponse>('/learning/knowledge')
      if (res?.ok) setKnowledge(res)
    } catch { /* offline */ }
  }, [])

  useEffect(() => {
    fetchStatus()
    fetchKnowledge()
  }, [fetchStatus, fetchKnowledge])

  // Poll while learning
  useEffect(() => {
    if (!isLearning) return
    const interval = setInterval(fetchStatus, 5000)
    return () => clearInterval(interval)
  }, [isLearning, fetchStatus])

  const startLearnCycle = async () => {
    setIsLearning(true)
    setError('')
    try {
      const res = await apiFetch<{ ok: boolean; job_id?: string; error?: string }>(
        '/learning/start',
        { method: 'POST', body: JSON.stringify({ full_pipeline: false }) }
      )
      if (res?.ok && res.job_id) {
        // Poll until done
        const poll = setInterval(async () => {
          try {
            const jobRes = await apiFetch<{ status: string }>(`/training/jobs/${res.job_id}`)
            if (jobRes?.status === 'completed' || jobRes?.status === 'failed') {
              clearInterval(poll)
              setIsLearning(false)
              fetchStatus()
              fetchKnowledge()
              if (jobRes.status === 'failed') setError('Learn cycle failed. Check logs.')
            }
          } catch {
            clearInterval(poll)
            setIsLearning(false)
          }
        }, 3000)
      } else {
        setError(res?.error || 'Failed to start learn cycle')
        setIsLearning(false)
      }
    } catch {
      setError('Failed to start learn cycle')
      setIsLearning(false)
    }
  }

  const resetLedger = async () => {
    if (!confirm('Reset all learning progress? This will not delete generated examples.')) return
    try {
      await apiFetch('/learning/reset', { method: 'POST' })
      fetchStatus()
      fetchKnowledge()
    } catch { /* offline */ }
  }

  return (
    <div className="p-6 space-y-6">
      {/* Status + Learn Now */}
      <div className="bg-surface-secondary rounded-xl p-5 border border-border">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold text-text-primary flex items-center gap-2">
            <span>🧠</span> Teacher-Student Learning
          </h3>
          <div className="flex gap-2">
            <button
              onClick={startLearnCycle}
              disabled={isLearning}
              className="px-4 py-2 bg-accent text-white text-sm rounded-lg hover:bg-accent-hover transition-colors disabled:opacity-50 cursor-pointer"
            >
              {isLearning ? 'Learning...' : 'Learn Now'}
            </button>
            {status && status.total_cycles > 0 && (
              <button
                onClick={resetLedger}
                className="px-3 py-2 bg-surface-tertiary text-text-muted text-xs rounded-lg hover:text-text-primary transition-colors cursor-pointer"
              >
                Reset
              </button>
            )}
          </div>
        </div>

        {isLearning && (
          <div className="mb-4 p-3 bg-accent/10 border border-accent/30 rounded-lg">
            <p className="text-sm text-accent animate-pulse">
              Processing... The teacher model is analyzing user data and generating training examples.
            </p>
          </div>
        )}

        {error && (
          <p className="text-sm text-red-400 mb-4">{error}</p>
        )}

        {/* Stats grid */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatCard label="Notes Processed" value={status?.processed_notes ?? 0} />
          <StatCard label="Sessions Processed" value={status?.processed_sessions ?? 0} />
          <StatCard label="Examples Generated" value={status?.total_examples ?? 0} />
          <StatCard label="Learn Cycles" value={status?.total_cycles ?? 0} />
        </div>

        {status?.last_sync_at && (
          <p className="text-xs text-text-muted mt-3">
            Last sync: {new Date(status.last_sync_at).toLocaleString()}
          </p>
        )}
      </div>

      {/* Knowledge Summary */}
      {knowledge && knowledge.summaries.length > 0 && (
        <div className="bg-surface-secondary rounded-xl p-5 border border-border">
          <h3 className="text-sm font-semibold text-text-primary mb-3 flex items-center gap-2">
            <span>📚</span> What the Pet Knows
          </h3>
          <div className="space-y-3">
            {knowledge.summaries.slice().reverse().map((s) => (
              <div key={s.cycle_id} className="bg-surface rounded-lg p-3 border border-border">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs font-medium text-accent">
                    Cycle {s.cycle_id}
                  </span>
                  <span className="text-xs text-text-muted">
                    {s.date ? new Date(s.date).toLocaleDateString() : ''}
                    {' · '}
                    {s.examples_generated} examples
                  </span>
                </div>
                <p className="text-sm text-text-secondary">{s.summary}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Cycle History */}
      {status && status.cycles.length > 0 && (
        <div className="bg-surface-secondary rounded-xl p-5 border border-border">
          <h3 className="text-sm font-semibold text-text-primary mb-3 flex items-center gap-2">
            <span>📋</span> Cycle History
          </h3>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-text-muted border-b border-border">
                  <th className="text-left py-2 pr-4">#</th>
                  <th className="text-left py-2 pr-4">Date</th>
                  <th className="text-right py-2 pr-4">Notes</th>
                  <th className="text-right py-2 pr-4">Sessions</th>
                  <th className="text-right py-2 pr-4">Examples</th>
                  <th className="text-left py-2">Model</th>
                </tr>
              </thead>
              <tbody>
                {status.cycles.slice().reverse().map((c) => (
                  <tr key={c.cycle_id} className="border-b border-border/50">
                    <td className="py-2 pr-4 text-text-secondary">{c.cycle_id}</td>
                    <td className="py-2 pr-4 text-text-secondary">
                      {c.started_at ? new Date(c.started_at).toLocaleDateString() : '-'}
                    </td>
                    <td className="py-2 pr-4 text-right text-text-primary">{c.notes_processed}</td>
                    <td className="py-2 pr-4 text-right text-text-primary">{c.sessions_processed}</td>
                    <td className="py-2 pr-4 text-right text-accent font-medium">{c.examples_generated}</td>
                    <td className="py-2 text-text-muted truncate max-w-[120px]">{c.model || '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Empty state */}
      {(!status || status.total_cycles === 0) && !isLearning && (
        <div className="text-center py-8 text-text-muted">
          <p className="text-3xl mb-2">🧠</p>
          <p className="text-sm">No learning cycles yet.</p>
          <p className="text-xs mt-1">
            Click "Learn Now" to pull data from the Pi and teach the pet about you.
          </p>
          <p className="text-xs mt-1 text-text-muted">
            Requires LM Studio running with a teacher model (e.g. Qwen 9B).
          </p>
        </div>
      )}
    </div>
  )
}

function StatCard({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-surface rounded-lg p-3 border border-border">
      <p className="text-xs text-text-muted mb-1">{label}</p>
      <p className="text-lg font-semibold text-text-primary">{value}</p>
    </div>
  )
}
