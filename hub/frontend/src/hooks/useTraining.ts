import { useState, useCallback, useRef, useEffect } from 'react'
import { apiFetch } from '../lib/api'

export interface PipelineStep {
  id: string
  name: string
  script: string
  description: string
  latest_job?: JobInfo
}

export interface JobInfo {
  job_id: string
  step: string
  script: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  start_time: number
  end_time: number
  return_code: number | null
  log_line_count: number
  elapsed_s: number
}

export interface ResearchEntry {
  iteration: number
  timestamp: string
  status: string
  config: {
    learning_rate: number
    lora_rank: number
    lora_alpha: number
    epochs: number
    batch_size: number
  }
  metrics: {
    perplexity?: number
    train_loss?: number
    eval_loss?: number
    train_time_s?: number
    eval_time_s?: number
    total_time_s?: number
  }
  is_best: boolean
  best_perplexity?: number
  error?: string
}

export function useTraining() {
  const [steps, setSteps] = useState<PipelineStep[]>([])
  const [activeJobId, setActiveJobId] = useState<string | null>(null)
  const [logLines, setLogLines] = useState<string[]>([])
  const [isRunning, setIsRunning] = useState(false)
  const [config, setConfig] = useState<Record<string, any>>({})
  const [results, setResults] = useState<Record<string, any>>({})
  const [viewingStepId, setViewingStepId] = useState<string | null>(null)
  const [researchLog, setResearchLog] = useState<ResearchEntry[]>([])
  const [researchBest, setResearchBest] = useState<ResearchEntry | null>(null)
  const [isAutoResearching, setIsAutoResearching] = useState(false)
  const [, setAutoResearchJobId] = useState<string | null>(null)
  const [autoResearchLogLines, setAutoResearchLogLines] = useState<string[]>([])
  const eventSourceRef = useRef<EventSource | null>(null)
  const autoResearchESRef = useRef<EventSource | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const researchPollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const fetchSteps = useCallback(async () => {
    try {
      const data = await apiFetch<{ steps: PipelineStep[] }>('/training/steps')
      setSteps(data.steps)
      return data.steps
    } catch (err) {
      console.error('Failed to fetch steps:', err)
      return []
    }
  }, [])

  // Auto-poll step status every 3s while any step is running
  useEffect(() => {
    const anyRunning = steps.some((s) => s.latest_job?.status === 'running')

    if (anyRunning && !pollRef.current) {
      pollRef.current = setInterval(() => {
        fetchSteps()
      }, 3000)
    } else if (!anyRunning && pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }

    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current)
        pollRef.current = null
      }
    }
  }, [steps, fetchSteps])

  const fetchConfig = useCallback(async () => {
    try {
      const data = await apiFetch<{ config: Record<string, any> }>(
        '/training/config'
      )
      setConfig(data.config)
    } catch (err) {
      console.error('Failed to fetch config:', err)
    }
  }, [])

  const fetchResults = useCallback(async () => {
    try {
      const data = await apiFetch('/training/results')
      setResults(data)
    } catch (err) {
      console.error('Failed to fetch results:', err)
    }
  }, [])

  const updateConfig = useCallback(
    async (updates: Record<string, any>) => {
      try {
        const data = await apiFetch<{ config: Record<string, any> }>(
          '/training/config',
          {
            method: 'PUT',
            body: JSON.stringify(updates),
          }
        )
        setConfig(data.config)
      } catch (err) {
        console.error('Failed to update config:', err)
      }
    },
    []
  )

  // View logs for a specific step (by clicking on it)
  const viewStepLogs = useCallback(
    (stepId: string) => {
      // Find the step and its job
      const step = steps.find((s) => s.id === stepId)
      if (!step?.latest_job) return

      const jobId = step.latest_job.job_id

      // If already viewing this step, toggle off
      if (viewingStepId === stepId) {
        setViewingStepId(null)
        setLogLines([])
        eventSourceRef.current?.close()
        eventSourceRef.current = null
        return
      }

      // Close existing EventSource if any
      eventSourceRef.current?.close()
      eventSourceRef.current = null
      setLogLines([])
      setViewingStepId(stepId)

      // Open SSE stream for this job's logs
      const es = new EventSource(`/api/training/logs/${jobId}`)
      eventSourceRef.current = es

      es.onmessage = (event) => {
        try {
          const parsed = JSON.parse(event.data)
          if (parsed.line !== undefined) {
            setLogLines((prev) => [...prev, parsed.line])
          }
          if (parsed.status) {
            // Job finished — refresh steps but keep logs visible
            fetchSteps()
          }
        } catch {
          // Skip unparseable
        }
      }

      es.onerror = () => {
        es.close()
        eventSourceRef.current = null
        fetchSteps()
      }
    },
    [steps, viewingStepId, fetchSteps]
  )

  const runStep = useCallback(
    async (step: string) => {
      try {
        setLogLines([])
        setIsRunning(true)
        setViewingStepId(step)

        const data = await apiFetch<{ job: JobInfo }>(`/training/run/${step}`, {
          method: 'POST',
          body: JSON.stringify({}),
        })

        const jobId = data.job.job_id
        setActiveJobId(jobId)

        // Immediately refresh steps so polling kicks in
        fetchSteps()

        // Close any existing EventSource
        eventSourceRef.current?.close()

        // Subscribe to SSE log stream
        const es = new EventSource(`/api/training/logs/${jobId}`)
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
              fetchSteps()
            }
          } catch {
            // Skip unparseable
          }
        }

        es.onerror = () => {
          setIsRunning(false)
          es.close()
          eventSourceRef.current = null
          fetchSteps()
        }
      } catch (err) {
        console.error('Failed to run step:', err)
        setIsRunning(false)
      }
    },
    [fetchSteps]
  )

  const stopJob = useCallback(async () => {
    if (!activeJobId) return
    try {
      await apiFetch(`/training/stop/${activeJobId}`, { method: 'POST' })
      eventSourceRef.current?.close()
      eventSourceRef.current = null
      setIsRunning(false)
      fetchSteps()
    } catch (err) {
      console.error('Failed to stop job:', err)
    }
  }, [activeJobId, fetchSteps])

  const resetJobs = useCallback(async () => {
    try {
      await apiFetch('/training/reset', { method: 'POST' })
      eventSourceRef.current?.close()
      eventSourceRef.current = null
      setIsRunning(false)
      setActiveJobId(null)
      setLogLines([])
      setViewingStepId(null)
      await fetchSteps()
    } catch (err) {
      console.error('Failed to reset jobs:', err)
    }
  }, [fetchSteps])

  // --- Auto-Research ---

  const fetchResearchLog = useCallback(async () => {
    try {
      const data = await apiFetch<{ entries: ResearchEntry[]; best: ResearchEntry | null }>(
        '/training/research-log'
      )
      setResearchLog(data.entries)
      setResearchBest(data.best)
    } catch (err) {
      console.error('Failed to fetch research log:', err)
    }
  }, [])

  const startAutoResearch = useCallback(
    async (strategy: string, budget: number, resume: boolean) => {
      try {
        setAutoResearchLogLines([])
        setIsAutoResearching(true)

        const data = await apiFetch<{ job: JobInfo }>('/training/autoresearch', {
          method: 'POST',
          body: JSON.stringify({ strategy, budget, resume }),
        })

        const jobId = data.job.job_id
        setAutoResearchJobId(jobId)

        // Close any existing auto-research EventSource
        autoResearchESRef.current?.close()

        // Subscribe to SSE log stream
        const es = new EventSource(`/api/training/logs/${jobId}`)
        autoResearchESRef.current = es

        // Poll research log every 5s while running
        researchPollRef.current = setInterval(() => {
          fetchResearchLog()
        }, 5000)

        es.onmessage = (event) => {
          try {
            const parsed = JSON.parse(event.data)
            if (parsed.line !== undefined) {
              setAutoResearchLogLines((prev) => [...prev, parsed.line])
            }
            if (parsed.status) {
              setIsAutoResearching(false)
              es.close()
              autoResearchESRef.current = null
              if (researchPollRef.current) {
                clearInterval(researchPollRef.current)
                researchPollRef.current = null
              }
              fetchResearchLog()
              fetchSteps()
            }
          } catch {
            // Skip unparseable
          }
        }

        es.onerror = () => {
          setIsAutoResearching(false)
          es.close()
          autoResearchESRef.current = null
          if (researchPollRef.current) {
            clearInterval(researchPollRef.current)
            researchPollRef.current = null
          }
          fetchResearchLog()
          fetchSteps()
        }

        return data.job
      } catch (err) {
        console.error('Failed to start auto-research:', err)
        setIsAutoResearching(false)
        throw err
      }
    },
    [fetchSteps, fetchResearchLog]
  )

  const stopAutoResearch = useCallback(async () => {
    try {
      // Send graceful stop signal (finishes current iteration)
      await apiFetch('/training/autoresearch/stop', { method: 'POST' })
    } catch (err) {
      console.error('Failed to stop auto-research:', err)
    }
  }, [])

  const clearResearchLog = useCallback(async () => {
    try {
      await apiFetch('/training/research-log', { method: 'DELETE' })
      setResearchLog([])
      setResearchBest(null)
    } catch (err) {
      console.error('Failed to clear research log:', err)
    }
  }, [])

  return {
    steps,
    activeJobId,
    logLines,
    isRunning,
    config,
    results,
    viewingStepId,
    fetchSteps,
    fetchConfig,
    fetchResults,
    updateConfig,
    runStep,
    stopJob,
    viewStepLogs,
    resetJobs,
    // Auto-Research
    researchLog,
    researchBest,
    isAutoResearching,
    autoResearchLogLines,
    fetchResearchLog,
    startAutoResearch,
    stopAutoResearch,
    clearResearchLog,
  }
}
