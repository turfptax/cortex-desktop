import { useState, useEffect } from 'react'
import { LogViewer } from './LogViewer'
import type { ResearchEntry } from '../../hooks/useTraining'

interface Props {
  researchLog: ResearchEntry[]
  researchBest: ResearchEntry | null
  isAutoResearching: boolean
  autoResearchLogLines: string[]
  fetchResearchLog: () => Promise<void>
  startAutoResearch: (strategy: string, budget: number, resume: boolean) => Promise<any>
  stopAutoResearch: () => Promise<void>
  clearResearchLog: () => Promise<void>
}

type SortKey = 'iteration' | 'perplexity' | 'train_loss' | 'learning_rate' | 'total_time_s'

export function AutoResearchPanel({
  researchLog,
  researchBest,
  isAutoResearching,
  autoResearchLogLines,
  fetchResearchLog,
  startAutoResearch,
  stopAutoResearch,
  clearResearchLog,
}: Props) {
  const [strategy, setStrategy] = useState('random')
  const [budget, setBudget] = useState(10)
  const [resume, setResume] = useState(false)
  const [sortKey, setSortKey] = useState<SortKey>('iteration')
  const [sortAsc, setSortAsc] = useState(true)
  const [showLogs, setShowLogs] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchResearchLog()
  }, [fetchResearchLog])

  // Auto-show logs when research starts
  useEffect(() => {
    if (isAutoResearching) {
      setShowLogs(true)
    }
  }, [isAutoResearching])

  const handleStart = async () => {
    setError(null)
    try {
      await startAutoResearch(strategy, budget, resume)
    } catch (err: any) {
      setError(err.message || 'Failed to start')
    }
  }

  const handleClear = async () => {
    if (!confirm('Clear all research results? This cannot be undone.')) return
    await clearResearchLog()
  }

  // Sort entries
  const sortedEntries = [...researchLog].sort((a, b) => {
    let aVal: number, bVal: number
    switch (sortKey) {
      case 'iteration':
        aVal = a.iteration; bVal = b.iteration; break
      case 'perplexity':
        aVal = a.metrics?.perplexity ?? Infinity; bVal = b.metrics?.perplexity ?? Infinity; break
      case 'train_loss':
        aVal = a.metrics?.train_loss ?? Infinity; bVal = b.metrics?.train_loss ?? Infinity; break
      case 'learning_rate':
        aVal = a.config?.learning_rate ?? 0; bVal = b.config?.learning_rate ?? 0; break
      case 'total_time_s':
        aVal = a.metrics?.total_time_s ?? 0; bVal = b.metrics?.total_time_s ?? 0; break
      default:
        aVal = a.iteration; bVal = b.iteration
    }
    return sortAsc ? aVal - bVal : bVal - aVal
  })

  const okCount = researchLog.filter(e => e.status === 'ok').length
  const failCount = researchLog.filter(e => e.status !== 'ok').length
  const bestPpl = researchBest?.metrics?.perplexity

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortAsc(!sortAsc)
    } else {
      setSortKey(key)
      setSortAsc(key === 'iteration')
    }
  }

  const SortHeader = ({ label, sortKeyName }: { label: string; sortKeyName: SortKey }) => (
    <th
      className="px-2 py-2 text-left text-xs font-medium text-text-muted cursor-pointer hover:text-text-primary select-none"
      onClick={() => toggleSort(sortKeyName)}
    >
      {label}
      {sortKey === sortKeyName && (
        <span className="ml-1">{sortAsc ? '\u25B2' : '\u25BC'}</span>
      )}
    </th>
  )

  return (
    <div className="flex flex-col h-full">
      {/* Config Section */}
      <div className="p-6 space-y-4">
        <div className="bg-surface-secondary rounded-xl p-5 border border-border">
          <h3 className="text-sm font-semibold text-text-primary mb-4">
            Auto-Research Configuration
          </h3>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
            {/* Strategy */}
            <div>
              <label className="block text-xs text-text-muted mb-1">Strategy</label>
              <select
                value={strategy}
                onChange={e => setStrategy(e.target.value)}
                disabled={isAutoResearching}
                className="w-full px-3 py-2 rounded-lg bg-surface border border-border text-sm text-text-primary focus:outline-none focus:ring-1 focus:ring-accent"
              >
                <option value="random">Random</option>
                <option value="grid">Grid</option>
                <option value="smart">Smart (Mutate Best)</option>
              </select>
            </div>

            {/* Budget */}
            <div>
              <label className="block text-xs text-text-muted mb-1">Budget (iterations)</label>
              <input
                type="number"
                value={budget}
                onChange={e => setBudget(Math.max(1, parseInt(e.target.value) || 1))}
                disabled={isAutoResearching}
                min={1}
                max={500}
                className="w-full px-3 py-2 rounded-lg bg-surface border border-border text-sm text-text-primary focus:outline-none focus:ring-1 focus:ring-accent"
              />
            </div>

            {/* Resume */}
            <div className="flex items-end">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={resume}
                  onChange={e => setResume(e.target.checked)}
                  disabled={isAutoResearching}
                  className="w-4 h-4 rounded border-border accent-accent"
                />
                <span className="text-sm text-text-secondary">Resume from log</span>
              </label>
            </div>

            {/* Actions */}
            <div className="flex items-end gap-2">
              {!isAutoResearching ? (
                <button
                  onClick={handleStart}
                  className="px-4 py-2 rounded-lg bg-accent text-white text-sm font-medium hover:bg-accent/90 transition-colors cursor-pointer"
                >
                  Start Research
                </button>
              ) : (
                <button
                  onClick={stopAutoResearch}
                  className="px-4 py-2 rounded-lg bg-danger text-white text-sm font-medium hover:bg-danger/90 transition-colors cursor-pointer"
                >
                  Stop (after iter)
                </button>
              )}
            </div>
          </div>

          {error && (
            <p className="text-xs text-danger mt-2">{error}</p>
          )}

          {/* Strategy description */}
          <p className="text-xs text-text-muted">
            {strategy === 'random' && 'Samples hyperparameters randomly within search bounds. Good for broad exploration.'}
            {strategy === 'grid' && 'Enumerates all combinations from a predefined grid. Systematic but may be slow for large spaces.'}
            {strategy === 'smart' && 'Mutates the best-so-far config with small perturbations. Best after initial exploration.'}
          </p>
        </div>

        {/* Summary Cards */}
        {researchLog.length > 0 && (
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            <SummaryCard label="Iterations" value={researchLog.length} />
            <SummaryCard label="Completed" value={okCount} />
            <SummaryCard label="Failed" value={failCount} accent={failCount > 0 ? 'danger' : undefined} />
            <SummaryCard
              label="Best Perplexity"
              value={bestPpl?.toFixed(2) ?? '-'}
              accent="success"
            />
            <SummaryCard
              label="Best Config"
              value={researchBest ? `r${researchBest.config.lora_rank} a${researchBest.config.lora_alpha}` : '-'}
            />
          </div>
        )}

        {/* Results Table */}
        {researchLog.length > 0 && (
          <div className="bg-surface-secondary rounded-xl border border-border overflow-hidden">
            <div className="px-5 py-3 border-b border-border flex items-center justify-between">
              <h3 className="text-sm font-semibold text-text-primary">
                Research Results ({researchLog.length} iterations)
              </h3>
              <div className="flex gap-2">
                <button
                  onClick={fetchResearchLog}
                  className="px-3 py-1.5 rounded-lg bg-surface-tertiary text-text-muted text-xs hover:text-text-primary transition-colors cursor-pointer"
                >
                  Refresh
                </button>
                <button
                  onClick={handleClear}
                  disabled={isAutoResearching}
                  className="px-3 py-1.5 rounded-lg bg-surface-tertiary text-text-muted text-xs hover:text-danger transition-colors cursor-pointer disabled:opacity-50"
                >
                  Clear Log
                </button>
              </div>
            </div>
            <div className="overflow-x-auto max-h-[400px] overflow-y-auto">
              <table className="w-full text-xs">
                <thead className="bg-surface-tertiary sticky top-0">
                  <tr>
                    <SortHeader label="#" sortKeyName="iteration" />
                    <th className="px-2 py-2 text-left text-xs font-medium text-text-muted">Status</th>
                    <SortHeader label="LR" sortKeyName="learning_rate" />
                    <th className="px-2 py-2 text-left text-xs font-medium text-text-muted">Rank</th>
                    <th className="px-2 py-2 text-left text-xs font-medium text-text-muted">Alpha</th>
                    <th className="px-2 py-2 text-left text-xs font-medium text-text-muted">Ep</th>
                    <th className="px-2 py-2 text-left text-xs font-medium text-text-muted">Batch</th>
                    <SortHeader label="Perplexity" sortKeyName="perplexity" />
                    <SortHeader label="Train Loss" sortKeyName="train_loss" />
                    <SortHeader label="Time" sortKeyName="total_time_s" />
                    <th className="px-2 py-2 text-left text-xs font-medium text-text-muted">Best</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {sortedEntries.map((entry, i) => {
                    const isBest = researchBest?.iteration === entry.iteration && entry.status === 'ok'
                    return (
                      <tr
                        key={i}
                        className={`${
                          isBest
                            ? 'bg-success/10'
                            : entry.status !== 'ok'
                              ? 'bg-danger/5'
                              : 'hover:bg-surface-tertiary'
                        } transition-colors`}
                      >
                        <td className="px-2 py-1.5 font-mono text-text-secondary">{entry.iteration}</td>
                        <td className="px-2 py-1.5">
                          <StatusBadge status={entry.status} />
                        </td>
                        <td className="px-2 py-1.5 font-mono text-text-secondary">
                          {entry.config?.learning_rate?.toExponential(1) ?? '-'}
                        </td>
                        <td className="px-2 py-1.5 font-mono text-text-secondary">
                          {entry.config?.lora_rank ?? '-'}
                        </td>
                        <td className="px-2 py-1.5 font-mono text-text-secondary">
                          {entry.config?.lora_alpha ?? '-'}
                        </td>
                        <td className="px-2 py-1.5 font-mono text-text-secondary">
                          {entry.config?.epochs ?? '-'}
                        </td>
                        <td className="px-2 py-1.5 font-mono text-text-secondary">
                          {entry.config?.batch_size ?? '-'}
                        </td>
                        <td className={`px-2 py-1.5 font-mono ${isBest ? 'text-success font-semibold' : 'text-text-secondary'}`}>
                          {entry.metrics?.perplexity?.toFixed(2) ?? '-'}
                        </td>
                        <td className="px-2 py-1.5 font-mono text-text-secondary">
                          {entry.metrics?.train_loss?.toFixed(4) ?? '-'}
                        </td>
                        <td className="px-2 py-1.5 font-mono text-text-secondary">
                          {entry.metrics?.total_time_s ? `${entry.metrics.total_time_s.toFixed(0)}s` : '-'}
                        </td>
                        <td className="px-2 py-1.5">
                          {entry.is_best && (
                            <span className="text-success font-semibold">*</span>
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Empty state */}
        {researchLog.length === 0 && !isAutoResearching && (
          <div className="text-center py-12 text-text-muted">
            <p className="text-3xl mb-2">&#x1F50D;</p>
            <p className="text-sm">No research results yet.</p>
            <p className="text-xs mt-1">Configure and start auto-research to find optimal hyperparameters.</p>
          </div>
        )}

        {/* Log toggle */}
        {(autoResearchLogLines.length > 0 || isAutoResearching) && (
          <button
            onClick={() => setShowLogs(!showLogs)}
            className="text-xs text-text-muted hover:text-text-primary transition-colors cursor-pointer"
          >
            {showLogs ? 'Hide' : 'Show'} live output ({autoResearchLogLines.length} lines)
          </button>
        )}
      </div>

      {/* Log viewer */}
      {showLogs && (autoResearchLogLines.length > 0 || isAutoResearching) && (
        <div className="flex-1 min-h-[200px] border-t border-border">
          <LogViewer
            lines={autoResearchLogLines}
            isRunning={isAutoResearching}
            stepName="Auto-Research"
          />
        </div>
      )}
    </div>
  )
}


function SummaryCard({
  label,
  value,
  accent,
}: {
  label: string
  value: any
  accent?: 'success' | 'danger'
}) {
  return (
    <div className="bg-surface-secondary rounded-lg p-3 border border-border">
      <p className="text-xs text-text-muted mb-1">{label}</p>
      <p
        className={`text-sm font-semibold ${
          accent === 'success'
            ? 'text-success'
            : accent === 'danger'
              ? 'text-danger'
              : 'text-text-primary'
        }`}
      >
        {value}
      </p>
    </div>
  )
}


function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    ok: 'bg-success/20 text-success',
    train_failed: 'bg-danger/20 text-danger',
    eval_failed: 'bg-danger/20 text-danger',
    eval_no_results: 'bg-warning/20 text-warning',
    eval_parse_error: 'bg-warning/20 text-warning',
    pending: 'bg-surface-tertiary text-text-muted',
  }

  return (
    <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-medium ${colors[status] || colors.pending}`}>
      {status === 'ok' ? 'OK' : status.replace(/_/g, ' ')}
    </span>
  )
}
