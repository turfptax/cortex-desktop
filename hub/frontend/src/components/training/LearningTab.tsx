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
  is_running?: boolean
  progress?: Progress | null
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
  servers_used?: number
  total_workers?: number
}

interface KnowledgeResponse {
  ok: boolean
  summaries: { cycle_id: number; date: string; summary: string; examples_generated: number }[]
  total_cycles: number
  total_examples: number
}

interface ServerStat {
  name: string
  model: string
  completed: number
  examples: number
  active: number
  avg_time: number
  parallel: number
}

interface Progress {
  phase: string
  completed: number
  total: number
  examples_so_far: number
  servers_active: number
  total_workers: number
  server_stats?: ServerStat[]
}

interface ServerInfo {
  url: string
  name: string
  parallel: number
  enabled: boolean
  online?: boolean
  models?: string[]
}

export function LearningTab() {
  const [status, setStatus] = useState<LearningStatus | null>(null)
  const [knowledge, setKnowledge] = useState<KnowledgeResponse | null>(null)
  const [isLearning, setIsLearning] = useState(false)
  const [error, setError] = useState('')
  const [progress, setProgress] = useState<Progress | null>(null)
  const [servers, setServers] = useState<ServerInfo[]>([])
  const [showServers, setShowServers] = useState(false)
  const [scanning, setScanning] = useState(false)
  const [newServerUrl, setNewServerUrl] = useState('')

  const fetchStatus = useCallback(async () => {
    try {
      const res = await apiFetch<LearningStatus>('/learning/status')
      if (res?.ok) {
        setStatus(res)
        if (res.progress) setProgress(res.progress)
      }
    } catch { /* offline */ }
  }, [])

  const fetchKnowledge = useCallback(async () => {
    try {
      const res = await apiFetch<KnowledgeResponse>('/learning/knowledge')
      if (res?.ok) setKnowledge(res)
    } catch { /* offline */ }
  }, [])

  const fetchServers = useCallback(async () => {
    try {
      const res = await apiFetch<{ ok: boolean; servers: ServerInfo[] }>('/learning/servers')
      if (res?.ok) setServers(res.servers)
    } catch { /* offline */ }
  }, [])

  useEffect(() => {
    fetchStatus()
    fetchKnowledge()
    fetchServers()
  }, [fetchStatus, fetchKnowledge, fetchServers])

  // Poll while learning
  useEffect(() => {
    if (!isLearning) return
    const interval = setInterval(async () => {
      try {
        const p = await apiFetch<{ running: boolean; progress: Progress }>('/learning/progress')
        if (p) {
          setProgress(p.progress)
          if (!p.running) {
            // Done — fetch final result
            const r = await apiFetch<{ running: boolean; result: { ok: boolean; error?: string } | null }>('/learning/result')
            if (r?.result) {
              setIsLearning(false)
              setProgress(null)
              fetchStatus()
              fetchKnowledge()
              if (!r.result.ok) setError(r.result.error || 'Learn cycle failed. Check logs.')
            }
          }
        }
      } catch {
        setIsLearning(false)
        setProgress(null)
      }
    }, 2000)
    return () => clearInterval(interval)
  }, [isLearning, fetchStatus, fetchKnowledge])

  const startLearnCycle = async () => {
    setIsLearning(true)
    setError('')
    setProgress(null)
    try {
      const enabledServers = servers.filter(s => s.enabled && s.online)
      const res = await apiFetch<{ ok: boolean; error?: string }>(
        '/learning/start',
        {
          method: 'POST',
          body: JSON.stringify({
            full_pipeline: false,
            servers: enabledServers.length > 0 ? enabledServers.map(s => ({
              url: s.url, name: s.name, parallel: s.parallel, enabled: true,
            })) : null,
          }),
        }
      )
      if (!res?.ok) {
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

  const scanNetwork = async () => {
    setScanning(true)
    try {
      const res = await apiFetch<{ ok: boolean; found: { ip: string; url: string; models: string[] }[] }>(
        '/learning/servers/scan', { method: 'POST' }
      )
      if (res?.ok && res.found.length > 0) {
        // Merge found servers with existing (don't duplicate)
        const existing = new Set(servers.map(s => s.url))
        const newServers = [...servers]
        for (const f of res.found) {
          if (!existing.has(f.url)) {
            newServers.push({
              url: f.url, name: f.ip, parallel: 4, enabled: true,
              online: true, models: f.models,
            })
          }
        }
        setServers(newServers)
        await saveServers(newServers)
      }
    } catch { /* offline */ }
    setScanning(false)
  }

  const addServer = async () => {
    if (!newServerUrl.trim()) return
    let url = newServerUrl.trim()
    if (!url.startsWith('http')) url = `http://${url}`
    if (!url.includes(':')) url += ':1234'
    if (!url.endsWith('/v1')) url += '/v1'

    const res = await apiFetch<{ ok: boolean; url: string; models: string[] }>(
      '/learning/servers/check', { method: 'POST', body: JSON.stringify({ url }) }
    )
    const newServer: ServerInfo = {
      url: res?.url || url,
      name: new URL(url).hostname,
      parallel: 4,
      enabled: true,
      online: res?.ok || false,
      models: res?.models || [],
    }
    const updated = [...servers, newServer]
    setServers(updated)
    await saveServers(updated)
    setNewServerUrl('')
  }

  const updateServer = async (index: number, changes: Partial<ServerInfo>) => {
    const updated = servers.map((s, i) => i === index ? { ...s, ...changes } : s)
    setServers(updated)
    await saveServers(updated)
  }

  const removeServer = async (index: number) => {
    const updated = servers.filter((_, i) => i !== index)
    setServers(updated)
    await saveServers(updated)
  }

  const saveServers = async (s: ServerInfo[]) => {
    await apiFetch('/learning/servers', {
      method: 'POST',
      body: JSON.stringify({ servers: s.map(({ url, name, parallel, enabled }) => ({ url, name, parallel, enabled })) }),
    })
  }

  const totalWorkers = servers.filter(s => s.enabled && s.online).reduce((sum, s) => sum + s.parallel, 0)

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
              onClick={() => setShowServers(!showServers)}
              className="px-3 py-2 bg-surface-tertiary text-text-muted text-xs rounded-lg hover:text-text-primary transition-colors cursor-pointer"
            >
              Servers ({servers.filter(s => s.online).length}/{servers.length})
            </button>
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

        {/* Progress bar */}
        {isLearning && progress && (
          <div className="mb-4 space-y-2">
            <div className="flex items-center justify-between text-xs text-text-muted">
              <span>
                {progress.phase === 'starting' && 'Starting...'}
                {progress.phase === 'connecting_pi' && 'Connecting to Pi...'}
                {progress.phase === 'pulling_data' && 'Pulling data from Pi...'}
                {progress.phase === 'synthesizing' && `Synthesizing ${progress.completed}/${progress.total} items`}
                {progress.phase === 'summarizing' && 'Generating knowledge summary...'}
                {progress.phase === 'done' && 'Complete!'}
                {progress.phase === 'error' && 'Error'}
              </span>
              <span>
                {progress.examples_so_far > 0 && `${progress.examples_so_far} examples`}
                {progress.servers_active > 0 && ` · ${progress.servers_active} server${progress.servers_active > 1 ? 's' : ''} · ${progress.total_workers} workers`}
              </span>
            </div>
            <div className="w-full bg-surface rounded-full h-2.5">
              <div
                className="bg-accent h-2.5 rounded-full transition-all duration-500"
                style={{
                  width: progress.total > 0
                    ? `${Math.round((progress.completed / progress.total) * 100)}%`
                    : progress.phase === 'done' ? '100%' : '5%',
                }}
              />
            </div>
            {progress.total > 0 && (
              <p className="text-xs text-text-muted text-right">
                {Math.round((progress.completed / progress.total) * 100)}%
              </p>
            )}
          </div>
        )}

        {/* Live server stats during processing */}
        {isLearning && progress?.server_stats && progress.server_stats.length > 0 && (
          <div className="mb-4 grid grid-cols-1 md:grid-cols-3 gap-2">
            {progress.server_stats.map((ss) => (
              <div key={ss.name} className="bg-surface rounded-lg p-3 border border-border">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs font-medium text-text-primary truncate">{ss.name}</span>
                  <span className="text-xs text-text-muted">{ss.active}/{ss.parallel} active</span>
                </div>
                <div className="text-xs text-text-muted truncate mb-2">{ss.model}</div>
                <div className="flex items-center justify-between">
                  <span className="text-sm font-semibold text-accent">{ss.completed} done</span>
                  <span className="text-xs text-text-muted">{ss.examples} ex</span>
                  <span className="text-xs text-text-muted">
                    {ss.avg_time > 0 ? `~${ss.avg_time}s/item` : '—'}
                  </span>
                </div>
                {/* Mini progress bar showing this server's share */}
                {progress.total > 0 && (
                  <div className="mt-2 w-full bg-surface-tertiary rounded-full h-1">
                    <div
                      className="bg-accent/60 h-1 rounded-full transition-all duration-500"
                      style={{ width: `${Math.min(100, Math.round((ss.completed / progress.total) * 100 * progress.server_stats!.length))}%` }}
                    />
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {isLearning && !progress && (
          <div className="mb-4 p-3 bg-accent/10 border border-accent/30 rounded-lg">
            <p className="text-sm text-accent animate-pulse">Starting learn cycle...</p>
          </div>
        )}

        {error && <p className="text-sm text-red-400 mb-4">{error}</p>}

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

      {/* LM Studio Servers Panel */}
      {showServers && (
        <div className="bg-surface-secondary rounded-xl p-5 border border-border">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-semibold text-text-primary flex items-center gap-2">
              <span>🖥️</span> LM Studio Servers
              <span className="text-xs text-text-muted font-normal">
                ({totalWorkers} total worker{totalWorkers !== 1 ? 's' : ''})
              </span>
            </h3>
            <button
              onClick={scanNetwork}
              disabled={scanning}
              className="px-3 py-2 bg-surface-tertiary text-text-muted text-xs rounded-lg hover:text-text-primary transition-colors disabled:opacity-50 cursor-pointer"
            >
              {scanning ? 'Scanning...' : 'Scan Network'}
            </button>
          </div>

          {/* Server list */}
          <div className="space-y-2 mb-4">
            {servers.map((server, i) => (
              <div key={i} className="flex items-center gap-3 bg-surface rounded-lg p-3 border border-border">
                <div className={`w-2 h-2 rounded-full flex-shrink-0 ${server.online ? 'bg-green-400' : 'bg-red-400'}`} />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm text-text-primary font-medium truncate">
                      {server.name || server.url}
                    </span>
                    {server.models && server.models.length > 0 && (
                      <span className="text-xs text-text-muted truncate">
                        {server.models[0]}
                      </span>
                    )}
                  </div>
                  <span className="text-xs text-text-muted">{server.url}</span>
                </div>
                <div className="flex items-center gap-2 flex-shrink-0">
                  <label className="text-xs text-text-muted">Parallel:</label>
                  <input
                    type="range"
                    min={1}
                    max={8}
                    value={server.parallel}
                    onChange={(e) => updateServer(i, { parallel: parseInt(e.target.value) })}
                    className="w-16 h-1 accent-accent"
                  />
                  <span className="text-xs text-text-primary w-4 text-center">{server.parallel}</span>
                  <button
                    onClick={() => updateServer(i, { enabled: !server.enabled })}
                    className={`px-2 py-1 text-xs rounded cursor-pointer ${
                      server.enabled
                        ? 'bg-accent/20 text-accent'
                        : 'bg-surface-tertiary text-text-muted'
                    }`}
                  >
                    {server.enabled ? 'On' : 'Off'}
                  </button>
                  <button
                    onClick={() => removeServer(i)}
                    className="px-2 py-1 text-xs text-red-400 hover:text-red-300 cursor-pointer"
                  >
                    ×
                  </button>
                </div>
              </div>
            ))}
          </div>

          {/* Add server */}
          <div className="flex gap-2">
            <input
              type="text"
              value={newServerUrl}
              onChange={(e) => setNewServerUrl(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && addServer()}
              placeholder="IP or URL (e.g. 10.0.0.105)"
              className="flex-1 bg-surface border border-border rounded-lg px-3 py-2 text-sm text-text-primary placeholder-text-muted focus:outline-none focus:border-accent"
            />
            <button
              onClick={addServer}
              className="px-4 py-2 bg-accent text-white text-sm rounded-lg hover:bg-accent-hover transition-colors cursor-pointer"
            >
              Add
            </button>
          </div>
        </div>
      )}

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
                    <td className="py-2 text-text-muted truncate max-w-[120px]">
                      {c.model || '-'}
                      {c.servers_used && c.servers_used > 1 ? ` (${c.servers_used} servers)` : ''}
                    </td>
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
            Requires LM Studio running with a teacher model. Click "Servers" to configure.
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
