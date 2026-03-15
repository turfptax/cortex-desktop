import { useState, useEffect, useCallback, useRef } from 'react'
import { apiFetch } from '../lib/api'

export interface DataSourceOptions {
  include_learned: boolean
  include_synthetic: boolean
  include_curated: boolean
  run_learn_cycle: boolean
}

export interface DreamState {
  active: boolean
  current_step: string | null
  current_step_name: string | null
  steps_completed: string[]
  steps_total: number
  errors: string[]
  started_at: string | null
  completed_at: string | null
  metrics: Record<string, any> | null
  trigger: string | null
}

export interface DataAvailability {
  learned_examples: number
  synthetic_examples: number
  curated_examples: number
}

const defaultState: DreamState = {
  active: false,
  current_step: null,
  current_step_name: null,
  steps_completed: [],
  steps_total: 6,
  errors: [],
  started_at: null,
  completed_at: null,
  metrics: null,
  trigger: null,
}

export function useDreamCycle() {
  const [dreamState, setDreamState] = useState<DreamState>(defaultState)
  const [isStarting, setIsStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [availability, setAvailability] = useState<DataAvailability | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Fetch data source counts
  const fetchAvailability = useCallback(async () => {
    try {
      const res = await apiFetch<any>('/training/availability')
      if (res?.ok) {
        setAvailability({
          learned_examples: res.learned_examples ?? 0,
          synthetic_examples: res.synthetic_examples ?? 0,
          curated_examples: res.curated_examples ?? 0,
        })
      }
    } catch { /* ignore */ }
  }, [])

  // Poll dream status
  const fetchStatus = useCallback(async () => {
    try {
      const state = await apiFetch<DreamState>('/training/dream-status')
      setDreamState(state)
      return state
    } catch {
      return null
    }
  }, [])

  // Start polling when dream is active
  useEffect(() => {
    if (dreamState.active && !pollRef.current) {
      pollRef.current = setInterval(fetchStatus, 3000)
    } else if (!dreamState.active && pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current)
        pollRef.current = null
      }
    }
  }, [dreamState.active, fetchStatus])

  // Check initial status on mount
  useEffect(() => {
    fetchStatus()
    fetchAvailability()
  }, [fetchStatus, fetchAvailability])

  // Start dream cycle: tuck in pet, then trigger dream on Hub
  const startDream = useCallback(async (options: DataSourceOptions) => {
    setIsStarting(true)
    setError(null)
    try {
      // 1. Tuck in the pet (put to sleep)
      await apiFetch('/pi/pet/tuck-in', { method: 'POST' })

      // 2. Start dream cycle directly on Hub with data source options
      const res = await apiFetch<any>('/training/dream-cycle', {
        method: 'POST',
        body: JSON.stringify({
          trigger: 'force_train',
          ...options,
        }),
      })

      if (res?.status === 'started') {
        // Start polling immediately
        setDreamState(prev => ({ ...prev, active: true }))
        setTimeout(fetchStatus, 1000)
      } else {
        setError(res?.error || 'Failed to start dream cycle')
      }
    } catch (err: any) {
      setError(err.message || 'Failed to start training')
    } finally {
      setIsStarting(false)
    }
  }, [fetchStatus])

  return {
    dreamState,
    isStarting,
    error,
    availability,
    startDream,
    fetchAvailability,
    fetchStatus,
  }
}
