import { useState, useCallback } from 'react'
import { apiFetch } from '../lib/api'

export interface PiStatus {
  online: boolean
  health?: any
  status?: any
  error?: string
}

export interface PetMessage {
  role: 'user' | 'assistant'
  content: string
  timestamp?: string
}

export function usePi() {
  const [piStatus, setPiStatus] = useState<PiStatus | null>(null)
  const [petMessages, setPetMessages] = useState<PetMessage[]>([])
  const [isPetLoading, setIsPetLoading] = useState(false)
  const [notes, setNotes] = useState<any[]>([])

  const fetchStatus = useCallback(async () => {
    try {
      const data = await apiFetch('/pi/status')
      setPiStatus(data)
    } catch {
      setPiStatus({ online: false, error: 'Backend unreachable' })
    }
  }, [])

  const fetchPetStatus = useCallback(async () => {
    try {
      return await apiFetch('/pi/pet/status')
    } catch {
      return null
    }
  }, [])

  const sendPetMessage = useCallback(async (prompt: string) => {
    setPetMessages((prev) => [
      ...prev,
      { role: 'user', content: prompt, timestamp: new Date().toISOString() },
    ])
    setIsPetLoading(true)

    try {
      const data = await apiFetch('/pi/pet/ask', {
        method: 'POST',
        body: JSON.stringify({ prompt }),
      })

      const response =
        data?.response || data?.result?.response || JSON.stringify(data)

      setPetMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: response,
          timestamp: new Date().toISOString(),
        },
      ])
    } catch (err: any) {
      setPetMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: `Error: ${err.message}`,
          timestamp: new Date().toISOString(),
        },
      ])
    } finally {
      setIsPetLoading(false)
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
    petMessages,
    isPetLoading,
    notes,
    fetchStatus,
    fetchPetStatus,
    sendPetMessage,
    fetchNotes,
    sendNote,
  }
}
