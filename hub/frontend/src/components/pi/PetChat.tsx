import { useState, useRef, useEffect, useCallback } from 'react'
import { type PetMessage } from '../../hooks/usePi'
import { apiFetch } from '../../lib/api'

interface PetInfo {
  bloom?: number
  stage_name?: string
  mood?: string
  interaction_count?: number
  intelligence?: number
}

interface Props {
  messages: PetMessage[]
  isLoading: boolean
  onSend: (prompt: string) => void
  isOnline: boolean
}

export function PetChat({ messages, isLoading, onSend, isOnline }: Props) {
  const [input, setInput] = useState('')
  const [petInfo, setPetInfo] = useState<PetInfo | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [messages])

  // Fetch pet info (bloom, stage, etc.) on mount and after each message
  const fetchPetInfo = useCallback(async () => {
    try {
      const res = await apiFetch('/pi/pet/status')
      if (res?.data) setPetInfo(res.data)
    } catch { /* offline */ }
  }, [])

  useEffect(() => {
    if (isOnline) fetchPetInfo()
  }, [isOnline, fetchPetInfo])

  // Refresh pet info when message count changes (after new response)
  useEffect(() => {
    if (isOnline && messages.length > 0) fetchPetInfo()
  }, [messages.length, isOnline, fetchPetInfo])

  const handleSubmit = () => {
    if (!input.trim() || isLoading) return
    onSend(input.trim())
    setInput('')
  }

  return (
    <div className="flex flex-col h-full">
      {/* Pet info header */}
      {isOnline && petInfo && (
        <div className="px-6 py-2 border-b border-border bg-surface-secondary/50 flex items-center gap-3">
          <span className="text-sm font-semibold text-text-primary">
            Cortex{petInfo.bloom ? ` · B${petInfo.bloom}` : ''}
          </span>
          {petInfo.stage_name && (
            <span className="text-xs text-text-muted">
              {petInfo.stage_name}
            </span>
          )}
          {petInfo.mood && (
            <span className={`text-xs px-1.5 py-0.5 rounded-full ${
              petInfo.mood === 'happy' ? 'bg-success/20 text-success'
              : petInfo.mood === 'content' ? 'bg-blue-500/20 text-blue-400'
              : petInfo.mood === 'sad' ? 'bg-red-500/20 text-red-400'
              : petInfo.mood === 'uneasy' ? 'bg-amber-500/20 text-amber-400'
              : 'bg-surface-tertiary text-text-muted'
            }`}>
              {petInfo.mood}
            </span>
          )}
          {petInfo.interaction_count != null && (
            <span className="text-xs text-text-muted ml-auto tabular-nums">
              {petInfo.interaction_count} chats
            </span>
          )}
        </div>
      )}

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-4 space-y-3">
        {!isOnline && (
          <div className="text-center py-8 text-text-muted">
            <p className="text-sm">Pi is offline. Connect to chat with your pet.</p>
          </div>
        )}
        {isOnline && messages.length === 0 && (
          <div className="text-center py-8 text-text-muted">
            <p className="text-3xl mb-2">🐾</p>
            <p className="text-sm">Chat with your Cortex Pet on the Pi</p>
          </div>
        )}
        {messages.map((msg, i) => (
          <div
            key={i}
            className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div
              className={`max-w-[75%] rounded-2xl px-4 py-2.5 ${
                msg.role === 'user'
                  ? 'bg-accent text-white rounded-br-md'
                  : 'bg-surface-tertiary text-text-primary rounded-bl-md'
              }`}
            >
              <p className="text-sm whitespace-pre-wrap">{msg.content}</p>
            </div>
          </div>
        ))}
        {isLoading && (
          <div className="flex justify-start">
            <div className="bg-surface-tertiary rounded-2xl rounded-bl-md px-4 py-3">
              <div className="flex gap-1.5">
                <span className="w-2 h-2 bg-text-muted rounded-full animate-bounce" />
                <span className="w-2 h-2 bg-text-muted rounded-full animate-bounce [animation-delay:0.1s]" />
                <span className="w-2 h-2 bg-text-muted rounded-full animate-bounce [animation-delay:0.2s]" />
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Input */}
      <div className="px-6 py-4 border-t border-border bg-surface-secondary">
        <div className="flex items-center gap-3">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSubmit()}
            placeholder={isOnline ? 'Talk to your pet...' : 'Pi is offline'}
            disabled={!isOnline || isLoading}
            className="flex-1 bg-surface text-text-primary text-sm rounded-xl px-4 py-3 border border-border focus:border-accent focus:outline-none disabled:opacity-50"
          />
          <button
            onClick={handleSubmit}
            disabled={!input.trim() || isLoading || !isOnline}
            className="px-4 py-3 rounded-xl bg-accent text-white font-medium text-sm hover:bg-accent-hover transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed shrink-0"
          >
            Send
          </button>
        </div>
      </div>
    </div>
  )
}
