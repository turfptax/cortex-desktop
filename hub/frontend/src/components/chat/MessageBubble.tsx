import { useState, useRef, useEffect } from 'react'
import { type ChatMessage } from '../../hooks/useChat'
import { apiFetch } from '../../lib/api'

interface Props {
  message: ChatMessage
  /** The user message that preceded this assistant message (for saving context) */
  previousUserMessage?: string
  isStreaming?: boolean
}

export function MessageBubble({ message, previousUserMessage, isStreaming }: Props) {
  const isUser = message.role === 'user'
  const isSystem = message.role === 'system'

  if (isSystem) return null

  // Summary marker — collapsed, expandable
  if (message.isSummary) {
    return (
      <div className="flex justify-center my-2">
        <details className="w-full max-w-[85%]">
          <summary className="flex items-center justify-center gap-2 cursor-pointer text-xs text-text-muted hover:text-text-secondary py-2 px-4 rounded-lg bg-surface-tertiary/50 border border-border/50">
            <span className="text-success">&#9679;</span>
            Memory compacted — earlier messages summarized
          </summary>
          <div className="mt-2 px-4 py-3 rounded-lg bg-surface-tertiary/30 border border-border/30">
            <p className="text-xs text-text-muted leading-relaxed whitespace-pre-wrap">
              {message.content.replace('[Earlier conversation summarized: ', '').replace(/]$/, '')}
            </p>
          </div>
        </details>
      </div>
    )
  }

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div className="flex flex-col gap-1 max-w-[75%]">
        <div
          className={`rounded-2xl px-4 py-2.5 ${
            isUser
              ? 'bg-accent text-white rounded-br-md'
              : 'bg-surface-tertiary text-text-primary rounded-bl-md'
          }`}
        >
          <p className="text-sm whitespace-pre-wrap leading-relaxed">
            {message.content}
            {!isUser && !message.content && (
              <span className="inline-block w-2 h-4 bg-text-muted animate-pulse rounded-sm" />
            )}
          </p>
        </div>

        {/* Feedback toolbar — only for assistant messages with content, not while streaming */}
        {!isUser && message.content && !isStreaming && (
          <FeedbackToolbar
            message={message}
            previousUserMessage={previousUserMessage}
          />
        )}
      </div>
    </div>
  )
}

// ─── Feedback Toolbar ───────────────────────────────────────────────

interface FeedbackToolbarProps {
  message: ChatMessage
  previousUserMessage?: string
}

function FeedbackToolbar({ message, previousUserMessage }: FeedbackToolbarProps) {
  const [feedback, setFeedback] = useState<'up' | 'down' | null>(null)
  const [isEditing, setIsEditing] = useState(false)
  const [editedContent, setEditedContent] = useState(message.content)
  const [saved, setSaved] = useState(false)
  const [saving, setSaving] = useState(false)
  const editRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (isEditing && editRef.current) {
      editRef.current.focus()
      editRef.current.style.height = 'auto'
      editRef.current.style.height = editRef.current.scrollHeight + 'px'
    }
  }, [isEditing])

  const handleSaveToTraining = async (correctedContent?: string) => {
    setSaving(true)
    try {
      const assistantContent = correctedContent || editedContent || message.content
      const messages = []

      if (previousUserMessage) {
        messages.push({ role: 'user', content: previousUserMessage })
      }
      messages.push({ role: 'assistant', content: assistantContent })

      await apiFetch('/training/dataset', {
        method: 'POST',
        body: JSON.stringify({
          messages,
          source: correctedContent ? 'correction' : feedback === 'up' ? 'approved' : 'chat',
          quality: feedback === 'up' ? 5 : feedback === 'down' ? 2 : 4,
          original_response: correctedContent ? message.content : '',
        }),
      })

      setSaved(true)
      setIsEditing(false)
      setTimeout(() => setSaved(false), 3000)
    } catch (err) {
      console.error('Failed to save example:', err)
    } finally {
      setSaving(false)
    }
  }

  const handleEdit = () => {
    setEditedContent(message.content)
    setIsEditing(true)
  }

  const handleCancelEdit = () => {
    setIsEditing(false)
    setEditedContent(message.content)
  }

  const handleSaveEdit = () => {
    handleSaveToTraining(editedContent)
  }

  return (
    <div className="flex flex-col gap-1">
      {/* Action buttons row */}
      <div className="flex items-center gap-1 px-1 opacity-60 hover:opacity-100 transition-opacity"
           style={{ opacity: feedback || saved ? 1 : undefined }}>
        {/* Thumbs up */}
        <button
          onClick={() => setFeedback(feedback === 'up' ? null : 'up')}
          className={`p-1 rounded transition-colors cursor-pointer ${
            feedback === 'up'
              ? 'text-success bg-success/10'
              : 'text-text-muted hover:text-success hover:bg-success/10'
          }`}
          title="Good response"
        >
          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M14 9V5a3 3 0 00-3-3l-4 9v11h11.28a2 2 0 002-1.7l1.38-9a2 2 0 00-2-2.3H14z" />
            <path strokeLinecap="round" strokeLinejoin="round" d="M7 22H4a2 2 0 01-2-2v-7a2 2 0 012-2h3" />
          </svg>
        </button>

        {/* Thumbs down */}
        <button
          onClick={() => setFeedback(feedback === 'down' ? null : 'down')}
          className={`p-1 rounded transition-colors cursor-pointer ${
            feedback === 'down'
              ? 'text-danger bg-danger/10'
              : 'text-text-muted hover:text-danger hover:bg-danger/10'
          }`}
          title="Bad response"
        >
          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M10 15v4a3 3 0 003 3l4-9V2H5.72a2 2 0 00-2 1.7l-1.38 9a2 2 0 002 2.3H10z" />
            <path strokeLinecap="round" strokeLinejoin="round" d="M17 2h3a2 2 0 012 2v7a2 2 0 01-2 2h-3" />
          </svg>
        </button>

        {/* Edit / correct response */}
        <button
          onClick={handleEdit}
          className="p-1 rounded text-text-muted hover:text-accent hover:bg-accent/10 transition-colors cursor-pointer"
          title="Edit and save corrected response"
        >
          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
          </svg>
        </button>

        {/* Save to training */}
        <button
          onClick={() => handleSaveToTraining()}
          disabled={saving || saved}
          className={`p-1 rounded transition-colors cursor-pointer ${
            saved
              ? 'text-success bg-success/10'
              : 'text-text-muted hover:text-accent hover:bg-accent/10'
          }`}
          title={saved ? 'Saved to training data' : 'Save to training dataset'}
        >
          {saved ? (
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
            </svg>
          ) : saving ? (
            <svg className="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <circle cx="12" cy="12" r="10" strokeDasharray="32" strokeDashoffset="12" />
            </svg>
          ) : (
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M8 7H5a2 2 0 00-2 2v9a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-3m-1 4l-3 3m0 0l-3-3m3 3V4" />
            </svg>
          )}
        </button>

        {saved && (
          <span className="text-xs text-success ml-1">Saved!</span>
        )}
      </div>

      {/* Inline editor */}
      {isEditing && (
        <div className="bg-surface rounded-lg border border-accent/50 p-2 mt-1">
          <p className="text-xs text-text-muted mb-1.5">
            Edit the response, then save as a corrected training example:
          </p>
          <textarea
            ref={editRef}
            value={editedContent}
            onChange={(e) => {
              setEditedContent(e.target.value)
              e.target.style.height = 'auto'
              e.target.style.height = e.target.scrollHeight + 'px'
            }}
            className="w-full bg-surface-tertiary text-text-primary text-sm rounded-md p-2 border border-border focus:border-accent focus:outline-none resize-none"
          />
          <div className="flex justify-end gap-2 mt-1.5">
            <button
              onClick={handleCancelEdit}
              className="px-2.5 py-1 rounded text-xs text-text-secondary hover:text-text-primary bg-surface-tertiary cursor-pointer"
            >
              Cancel
            </button>
            <button
              onClick={handleSaveEdit}
              disabled={saving || editedContent === message.content}
              className="px-2.5 py-1 rounded text-xs text-white bg-accent hover:bg-accent-hover cursor-pointer disabled:opacity-40"
            >
              {saving ? 'Saving...' : 'Save Correction'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
