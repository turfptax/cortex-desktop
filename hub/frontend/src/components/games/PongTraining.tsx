import { useState, useRef } from 'react'
import { LogViewer } from '../training/LogViewer'
import { apiFetch } from '../../lib/api'

const API_BASE = import.meta.env.VITE_API_URL || ''

export function PongTraining() {
  const [episodes, setEpisodes] = useState(50000)
  const [isRunning, setIsRunning] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const [logLines, setLogLines] = useState<string[]>([])
  const eventSourceRef = useRef<EventSource | null>(null)

  const startTraining = async () => {
    try {
      const data = await apiFetch<{ job: { id: string } }>('/games/train/pong', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ episodes }),
      })

      const id = data.job.id
      setJobId(id)
      setIsRunning(true)
      setLogLines([])

      // Connect SSE
      const es = new EventSource(`${API_BASE}/api/games/train/pong/logs/${id}`)
      eventSourceRef.current = es

      es.onmessage = (event) => {
        try {
          const parsed = JSON.parse(event.data)
          if (parsed.line !== undefined) {
            setLogLines((prev) => [...prev, parsed.line])
          }
          if (parsed.status) {
            setIsRunning(false)
            es.close()
            eventSourceRef.current = null
          }
        } catch {
          // skip unparseable
        }
      }

      es.onerror = () => {
        es.close()
        eventSourceRef.current = null
        setIsRunning(false)
      }
    } catch (err) {
      setLogLines((prev) => [...prev, `[ERROR] Failed to start: ${err}`])
    }
  }

  const stopTraining = async () => {
    if (!jobId) return
    try {
      await apiFetch(`/games/train/pong/stop/${jobId}`, { method: 'POST' })
      setIsRunning(false)
      eventSourceRef.current?.close()
      eventSourceRef.current = null
    } catch (err) {
      setLogLines((prev) => [...prev, `[ERROR] Failed to stop: ${err}`])
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Controls */}
      <div className="p-4 border-b border-border bg-surface-secondary">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <label className="text-xs text-text-secondary">Episodes:</label>
            <input
              type="number"
              value={episodes}
              onChange={(e) => setEpisodes(Number(e.target.value))}
              className="w-24 px-2 py-1 bg-surface-tertiary border border-border rounded text-xs text-text-primary"
              min={1000}
              step={5000}
              disabled={isRunning}
            />
          </div>

          {!isRunning ? (
            <button
              onClick={startTraining}
              className="px-4 py-1.5 bg-accent text-white rounded text-xs font-medium hover:bg-accent-hover transition-colors"
            >
              Start Training
            </button>
          ) : (
            <button
              onClick={stopTraining}
              className="px-4 py-1.5 bg-danger text-white rounded text-xs font-medium hover:opacity-90 transition-colors"
            >
              Stop
            </button>
          )}

          <span className="text-xs text-text-muted ml-auto">
            Q-learning trains a Pong AI overnight on the Pi
          </span>
        </div>
      </div>

      {/* Log output */}
      <div className="flex-1 overflow-hidden">
        <LogViewer lines={logLines} isRunning={isRunning} stepName="Pong AI Training" />
      </div>
    </div>
  )
}
