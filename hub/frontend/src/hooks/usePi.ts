import { useState, useCallback } from 'react'
import { apiFetch } from '../lib/api'

export interface PiStatus {
  online: boolean
  health?: any
  status?: any
  error?: string
}

export function usePi() {
  const [piStatus, setPiStatus] = useState<PiStatus | null>(null)
  const [notes, setNotes] = useState<any[]>([])

  const fetchStatus = useCallback(async () => {
    try {
      const data = await apiFetch('/pi/status')
      setPiStatus(data)
    } catch {
      setPiStatus({ online: false, error: 'Backend unreachable' })
    }
  }, [])

  const fetchNotes = useCallback(async (limit = 20) => {
    try {
      const data = await apiFetch(`/pi/notes?limit=${limit}`)
      setNotes(data?.results || data?.data || [])
    } catch {
      setNotes([])
    }
  }, [])

  const sendNote = useCallback(
    async (
      content: string,
      tags = '',
      project = '',
      noteType = 'note'
    ) => {
      try {
        await apiFetch('/pi/notes', {
          method: 'POST',
          body: JSON.stringify({
            content,
            tags,
            project,
            note_type: noteType,
          }),
        })
        await fetchNotes()
        return true
      } catch {
        return false
      }
    },
    [fetchNotes]
  )

  return {
    piStatus,
    notes,
    fetchStatus,
    fetchNotes,
    sendNote,
  }
}
