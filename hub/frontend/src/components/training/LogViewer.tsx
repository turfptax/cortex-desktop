import { useEffect, useRef } from 'react'

interface Props {
  lines: string[]
  isRunning?: boolean
  stepName?: string
}

export function LogViewer({ lines, isRunning, stepName }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to bottom
  useEffect(() => {
    const el = containerRef.current
    if (el) {
      el.scrollTop = el.scrollHeight
    }
  }, [lines])

  return (
    <div className="flex flex-col h-full">
      <div className="px-4 py-2 border-b border-border bg-surface-secondary flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-text-secondary">
            {stepName ? `Output — ${stepName}` : 'Output'}
          </span>
          {isRunning && (
            <span className="inline-block w-1.5 h-1.5 bg-success rounded-full animate-pulse" />
          )}
        </div>
        <span className="text-xs text-text-muted">
          {lines.length} lines
        </span>
      </div>
      <div
        ref={containerRef}
        className="flex-1 overflow-y-auto bg-[#0d0e12] p-4 font-mono text-xs leading-5"
      >
        {lines.length === 0 && (
          <div className="text-text-muted italic">
            {isRunning ? 'Waiting for output...' : 'No log output for this step.'}
          </div>
        )}
        {lines.map((line, i) => (
          <div
            key={i}
            className={`${
              line.startsWith('[ERROR]') || line.startsWith('ERROR')
                ? 'text-danger'
                : line.startsWith('[JOB')
                  ? 'text-accent'
                  : line.startsWith('WARNING')
                    ? 'text-warning'
                    : 'text-text-secondary'
            }`}
          >
            {line}
          </div>
        ))}
        {isRunning && (
          <span className="inline-block w-2 h-3.5 bg-text-muted animate-pulse" />
        )}
      </div>
    </div>
  )
}
