import { useState, useEffect } from 'react'
import { type PipelineStep as StepType } from '../../hooks/useTraining'

interface Props {
  step: StepType
  onRun: () => void
  onStop: () => void
  onViewLogs: () => void
  isRunning: boolean
  isViewingLogs: boolean
  compact?: boolean
  disabled?: boolean
}

const statusColors: Record<string, string> = {
  idle: 'bg-text-muted/20 text-text-muted',
  pending: 'bg-warning/20 text-warning',
  running: 'bg-accent/20 text-accent',
  completed: 'bg-success/20 text-success',
  failed: 'bg-danger/20 text-danger',
}

function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${Math.floor(seconds)}s`
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  if (mins < 60) return `${mins}m ${secs}s`
  const hrs = Math.floor(mins / 60)
  const remainMins = mins % 60
  return `${hrs}h ${remainMins}m`
}

export function PipelineStep({ step, onRun, onStop, onViewLogs, isRunning, isViewingLogs, compact, disabled }: Props) {
  const status = step.latest_job?.status || 'idle'
  const elapsed = step.latest_job?.elapsed_s

  // Live elapsed time ticker for running steps
  const [liveElapsed, setLiveElapsed] = useState<number>(0)

  useEffect(() => {
    if (status !== 'running' || !step.latest_job?.start_time) {
      setLiveElapsed(0)
      return
    }

    // Initialize from server elapsed
    if (elapsed !== undefined) {
      setLiveElapsed(elapsed)
    }

    const tick = setInterval(() => {
      const startTime = step.latest_job!.start_time
      const now = Date.now() / 1000
      setLiveElapsed(Math.max(0, now - startTime))
    }, 1000)

    return () => clearInterval(tick)
  }, [status, step.latest_job?.start_time, elapsed])

  const displayElapsed = status === 'running' ? liveElapsed : elapsed
  const hasJob = !!step.latest_job

  if (compact) {
    return (
      <div
        onClick={hasJob ? onViewLogs : undefined}
        className={`bg-surface-secondary rounded-lg p-2.5 border transition-colors ${
          isViewingLogs
            ? 'border-accent'
            : 'border-border hover:border-border/80'
        } ${hasJob ? 'cursor-pointer' : ''} ${disabled ? 'opacity-50' : ''}`}
      >
        <div className="flex items-center justify-between mb-1.5">
          <div className="flex items-center gap-1.5">
            <span className={`w-5 h-5 rounded flex items-center justify-center text-[10px] font-bold ${
              status === 'running'
                ? 'bg-accent/20 text-accent'
                : 'bg-surface-tertiary text-text-secondary'
            }`}>
              {step.id}
            </span>
            <span className="text-xs font-semibold text-text-primary truncate">{step.name}</span>
          </div>
          <span className={`px-1.5 py-0.5 rounded-full text-[10px] font-medium ${statusColors[status]}`}>
            {status === 'running' && (
              <span className="inline-block w-1 h-1 bg-current rounded-full mr-1 animate-pulse" />
            )}
            {status}
          </span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-[10px] text-text-muted">
            {displayElapsed !== undefined && displayElapsed > 0
              ? formatElapsed(displayElapsed)
              : '\u00A0'}
          </span>
          {isRunning ? (
            <button
              onClick={(e) => { e.stopPropagation(); onStop() }}
              className="px-2 py-0.5 rounded bg-danger text-white text-[10px] font-medium hover:bg-danger/80 transition-colors cursor-pointer"
            >
              Stop
            </button>
          ) : (
            <button
              onClick={(e) => { e.stopPropagation(); if (!disabled) onRun() }}
              disabled={disabled}
              className={`px-2 py-0.5 rounded text-[10px] font-medium transition-colors ${
                disabled
                  ? 'bg-surface-tertiary/50 text-text-muted/50 cursor-not-allowed opacity-40'
                  : 'bg-accent text-white hover:bg-accent-hover cursor-pointer'
              }`}
              title={disabled ? 'Script not available — see guidance above' : undefined}
            >
              Run
            </button>
          )}
        </div>
      </div>
    )
  }

  return (
    <div
      onClick={hasJob ? onViewLogs : undefined}
      className={`bg-surface-secondary rounded-xl p-4 border transition-colors ${
        isViewingLogs
          ? 'border-accent'
          : 'border-border hover:border-border/80'
      } ${hasJob ? 'cursor-pointer' : ''}`}
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          {/* Step number */}
          <div className={`w-8 h-8 rounded-lg flex items-center justify-center text-xs font-bold ${
            status === 'running'
              ? 'bg-accent/20 text-accent'
              : 'bg-surface-tertiary text-text-secondary'
          }`}>
            {step.id}
          </div>

          {/* Info */}
          <div>
            <h3 className="text-sm font-semibold text-text-primary">
              {step.name}
            </h3>
            <p className="text-xs text-text-muted">{step.description}</p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {/* Elapsed time */}
          {displayElapsed !== undefined && displayElapsed > 0 && (
            <span className={`text-xs font-mono ${
              status === 'running' ? 'text-accent' : 'text-text-muted'
            }`}>
              {formatElapsed(displayElapsed)}
            </span>
          )}

          {/* Status badge */}
          <span
            className={`px-2.5 py-1 rounded-full text-xs font-medium ${statusColors[status]}`}
          >
            {status === 'running' && (
              <span className="inline-block w-1.5 h-1.5 bg-current rounded-full mr-1.5 animate-pulse" />
            )}
            {status}
          </span>

          {/* Log line count indicator */}
          {hasJob && step.latest_job!.log_line_count > 0 && (
            <span className="text-xs text-text-muted" title="Log lines">
              {step.latest_job!.log_line_count} lines
            </span>
          )}

          {/* Action button */}
          {isRunning ? (
            <button
              onClick={(e) => {
                e.stopPropagation()
                onStop()
              }}
              className="px-4 py-2 rounded-lg bg-danger text-white text-xs font-medium hover:bg-danger/80 transition-colors cursor-pointer"
            >
              Stop
            </button>
          ) : (
            <button
              onClick={(e) => {
                e.stopPropagation()
                if (!disabled) onRun()
              }}
              disabled={disabled}
              className={`px-4 py-2 rounded-lg text-xs font-medium transition-colors ${
                disabled
                  ? 'bg-surface-tertiary/50 text-text-muted/50 cursor-not-allowed opacity-40'
                  : 'bg-accent text-white hover:bg-accent-hover cursor-pointer'
              }`}
              title={disabled ? 'Script not available — see guidance above' : undefined}
            >
              Run
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
