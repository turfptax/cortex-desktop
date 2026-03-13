import { useState, useCallback, useRef, useEffect } from 'react'
import { type PetStatus } from '../components/PetWidget'

export interface ChatMessage {
  role: 'system' | 'user' | 'assistant'
  content: string
  isSummary?: boolean // true if this message is a compacted summary
}

// ~4 chars per token is a rough English estimate for SmolLM2's tokenizer
function estimateTokens(text: string): number {
  return Math.ceil(text.length / 4)
}

function estimateConversationTokens(messages: ChatMessage[]): number {
  return messages.reduce(
    (sum, m) => sum + estimateTokens(m.content) + 4, // +4 for role/formatting overhead
    0
  )
}

// Thresholds (in estimated tokens) — LM Studio runs 8k context
const COMPACT_TRIGGER = 6000 // trigger compaction at ~6k, leaving room for response
const KEEP_RECENT = 6 // keep the last N messages uncompacted

// ── Prompt Template System ──────────────────────────────────────

const BUILTIN_PRESETS: Record<string, string> = {
  Default:
    'You are Cortex Pet, a friendly AI companion. Respond naturally in 1-4 sentences.',
  Tamagotchi: `You are Cortex Pet, a {stage_name}-stage AI companion. Your mood is {mood}.
Vitals — hunger: {hunger}, cleanliness: {cleanliness}, energy: {energy}, happiness: {happiness}.
Intelligence: {intelligence} IQ. You have had {interaction_count} conversations.
Respond naturally in 1-4 sentences. Let your mood and vitals subtly influence your tone.`,
}

const STORAGE_KEY = 'cortex-prompt-presets'
const ACTIVE_PRESET_KEY = 'cortex-active-preset'

/** All available template variables with descriptions */
export const TEMPLATE_VARIABLES: { key: string; desc: string }[] = [
  { key: 'mood', desc: 'Current mood (happy, neutral, etc.)' },
  { key: 'mood_score', desc: 'Mood score (-1 to 1)' },
  { key: 'stage_name', desc: 'Evolution stage name' },
  { key: 'stage', desc: 'Stage number (0-5)' },
  { key: 'hunger', desc: 'Hunger percentage' },
  { key: 'cleanliness', desc: 'Cleanliness percentage' },
  { key: 'energy', desc: 'Energy percentage' },
  { key: 'happiness', desc: 'Happiness percentage' },
  { key: 'intelligence', desc: 'IQ score (0-100)' },
  { key: 'is_coma', desc: 'Whether pet is in coma' },
  { key: 'interaction_count', desc: 'Total conversations' },
  { key: 'total_feeds', desc: 'Times fed' },
  { key: 'total_cleans', desc: 'Times cleaned' },
]

/** Replace {variable} placeholders with real values from PetStatus */
export function resolveTemplate(
  template: string,
  pet: PetStatus | null
): string {
  if (!pet) return template // Leave placeholders as-is if no pet data

  const vars: Record<string, string> = {
    mood: pet.mood ?? 'unknown',
    mood_score: (pet.mood_score ?? 0).toFixed(2),
    stage_name: pet.stage_name ?? 'Unknown',
    stage: String(pet.stage ?? 0),
    hunger: `${Math.round((pet.hunger ?? 1) * 100)}%`,
    cleanliness: `${Math.round((pet.cleanliness ?? 1) * 100)}%`,
    energy: `${Math.round((pet.energy ?? 1) * 100)}%`,
    happiness: `${Math.round((pet.happiness ?? 0.5) * 100)}%`,
    intelligence: String(Math.round(pet.intelligence ?? 0)),
    is_coma: String(pet.is_coma ?? false),
    interaction_count: String(pet.interaction_count ?? 0),
    total_feeds: String(pet.total_feeds ?? 0),
    total_cleans: String(pet.total_cleans ?? 0),
  }

  return template.replace(/\{(\w+)\}/g, (match, key) =>
    key in vars ? vars[key] : match
  )
}

function loadPresets(): Record<string, string> {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored) return JSON.parse(stored)
  } catch {
    // corrupt data
  }
  return {}
}

function savePresets(userPresets: Record<string, string>) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(userPresets))
}

function loadActivePresetName(): string {
  return localStorage.getItem(ACTIVE_PRESET_KEY) || 'Tamagotchi'
}

function saveActivePresetName(name: string) {
  localStorage.setItem(ACTIVE_PRESET_KEY, name)
}

// ── Summary Helper ──────────────────────────────────────────────

async function requestSummary(
  messages: ChatMessage[],
  model?: string
): Promise<string> {
  const conversationText = messages
    .map((m) => `${m.role}: ${m.content}`)
    .join('\n')

  const summaryMessages = [
    {
      role: 'system',
      content:
        'Summarize the following conversation into a brief paragraph (under 100 words). ' +
        "Capture key topics discussed, any important facts shared, the user's mood/intent, " +
        'and anything the assistant should remember. Be concise.',
    },
    {
      role: 'user',
      content: conversationText,
    },
  ]

  try {
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        messages: summaryMessages,
        model,
        temperature: 0.3,
        max_tokens: 200,
      }),
    })

    if (!resp.ok) return '[Summary failed]'

    const reader = resp.body?.getReader()
    if (!reader) return '[Summary failed]'

    const decoder = new TextDecoder()
    let result = ''
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        const trimmed = line.trim()
        if (!trimmed || !trimmed.startsWith('data: ')) continue
        const data = trimmed.slice(6)
        if (data === '[DONE]') break
        try {
          const chunk = JSON.parse(data)
          const delta = chunk.choices?.[0]?.delta?.content
          if (delta) result += delta
        } catch {
          // skip
        }
      }
    }

    return result.trim() || '[Empty summary]'
  } catch {
    return '[Summary failed]'
  }
}

// ── Main Hook ───────────────────────────────────────────────────

export function useChat(petStatus?: PetStatus | null) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [isStreaming, setIsStreaming] = useState(false)
  const [isCompacting, setIsCompacting] = useState(false)
  const [memoryContext, setMemoryContext] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  // ── Prompt Presets ──────────────────────────────────────────
  const [userPresets, setUserPresets] = useState<Record<string, string>>(
    loadPresets
  )
  const [activePresetName, setActivePresetName] = useState(
    loadActivePresetName
  )

  // All presets = builtins + user presets
  const allPresets: Record<string, string> = {
    ...BUILTIN_PRESETS,
    ...userPresets,
  }

  // The template is the active preset's content
  const [systemPrompt, setSystemPrompt] = useState(
    allPresets[activePresetName] ?? BUILTIN_PRESETS.Tamagotchi
  )

  // When user picks a different preset, load its template
  const selectPreset = useCallback(
    (name: string) => {
      const all = { ...BUILTIN_PRESETS, ...userPresets }
      if (name in all) {
        setActivePresetName(name)
        setSystemPrompt(all[name])
        saveActivePresetName(name)
      }
    },
    [userPresets]
  )

  // Save current template as a new (or overwrite) preset
  const savePreset = useCallback(
    (name: string) => {
      const updated = { ...userPresets, [name]: systemPrompt }
      setUserPresets(updated)
      savePresets(updated)
      setActivePresetName(name)
      saveActivePresetName(name)
    },
    [userPresets, systemPrompt]
  )

  // Delete a user preset (builtins can't be deleted)
  const deletePreset = useCallback(
    (name: string) => {
      if (name in BUILTIN_PRESETS) return // can't delete builtins
      const updated = { ...userPresets }
      delete updated[name]
      setUserPresets(updated)
      savePresets(updated)
      if (activePresetName === name) {
        selectPreset('Tamagotchi')
      }
    },
    [userPresets, activePresetName, selectPreset]
  )

  // Auto-save template changes back to the active preset if it's user-created
  useEffect(() => {
    if (activePresetName in userPresets) {
      const updated = { ...userPresets, [activePresetName]: systemPrompt }
      setUserPresets(updated)
      savePresets(updated)
    }
    // Only trigger on systemPrompt changes, not on userPresets changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [systemPrompt])

  // Resolve the template with current pet data
  const resolvedPrompt = resolveTemplate(systemPrompt, petStatus ?? null)

  // ── Build API Messages ──────────────────────────────────────

  const buildApiMessages = useCallback(
    (
      currentMessages: ChatMessage[],
      newUserMsg: ChatMessage
    ): ChatMessage[] => {
      const apiMessages: ChatMessage[] = []

      // System prompt (resolved with pet variables)
      if (resolvedPrompt) {
        let sysContent = resolvedPrompt
        if (memoryContext) {
          sysContent += `\n\n[Memory from earlier conversation: ${memoryContext}]`
        }
        apiMessages.push({ role: 'system', content: sysContent })
      }

      // Only include non-summary messages for the API
      for (const m of currentMessages) {
        if (!m.isSummary) {
          apiMessages.push({ role: m.role, content: m.content })
        }
      }

      apiMessages.push(newUserMsg)
      return apiMessages
    },
    [resolvedPrompt, memoryContext]
  )

  // ── Compaction ──────────────────────────────────────────────

  const maybeCompact = useCallback(
    async (currentMessages: ChatMessage[], model?: string) => {
      const tokens = estimateConversationTokens(currentMessages)

      if (tokens < COMPACT_TRIGGER) return currentMessages

      console.log(
        `[Memory] Context ~${tokens} tokens, compacting (threshold: ${COMPACT_TRIGGER})...`
      )
      setIsCompacting(true)

      const toSummarize = currentMessages.slice(0, -KEEP_RECENT)
      const toKeep = currentMessages.slice(-KEEP_RECENT)

      if (toSummarize.length < 2) {
        setIsCompacting(false)
        return currentMessages
      }

      const messagesForSummary: ChatMessage[] = []
      if (memoryContext) {
        messagesForSummary.push({
          role: 'system',
          content: `Previous context: ${memoryContext}`,
        })
      }
      messagesForSummary.push(...toSummarize)

      const summary = await requestSummary(messagesForSummary, model)
      const summaryTokens = estimateTokens(summary)

      console.log(
        `[Memory] Compacted ${toSummarize.length} messages (~${estimateConversationTokens(toSummarize)} tokens) ` +
          `into summary (~${summaryTokens} tokens)`
      )

      setMemoryContext(summary)

      const compactedMessages: ChatMessage[] = [
        {
          role: 'assistant',
          content: `[Earlier conversation summarized: ${summary}]`,
          isSummary: true,
        },
        ...toKeep,
      ]

      setMessages(compactedMessages)
      setIsCompacting(false)

      return compactedMessages
    },
    [memoryContext]
  )

  // ── Send Message ────────────────────────────────────────────

  const sendMessage = useCallback(
    async (content: string, model?: string, temperature?: number) => {
      const userMsg: ChatMessage = { role: 'user', content }

      const apiMessages = buildApiMessages(messages, userMsg)

      setMessages((prev) => [...prev, userMsg])
      setIsStreaming(true)

      const assistantMsg: ChatMessage = { role: 'assistant', content: '' }
      setMessages((prev) => [...prev, assistantMsg])

      const controller = new AbortController()
      abortRef.current = controller

      try {
        const resp = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            messages: apiMessages,
            model,
            temperature: temperature ?? 0.7,
          }),
          signal: controller.signal,
        })

        if (!resp.ok) {
          throw new Error(`Chat error: ${resp.status}`)
        }

        const reader = resp.body?.getReader()
        if (!reader) throw new Error('No response body')

        const decoder = new TextDecoder()
        let buffer = ''

        while (true) {
          const { done, value } = await reader.read()
          if (done) break

          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n')
          buffer = lines.pop() || ''

          for (const line of lines) {
            const trimmed = line.trim()
            if (!trimmed || !trimmed.startsWith('data: ')) continue
            const data = trimmed.slice(6)
            if (data === '[DONE]') break

            try {
              const chunk = JSON.parse(data)
              const delta = chunk.choices?.[0]?.delta?.content
              if (delta) {
                setMessages((prev) => {
                  const updated = [...prev]
                  const last = updated[updated.length - 1]
                  if (last.role === 'assistant') {
                    updated[updated.length - 1] = {
                      ...last,
                      content: last.content + delta,
                    }
                  }
                  return updated
                })
              }
            } catch {
              // Skip malformed chunks
            }
          }
        }
      } catch (err: any) {
        if (err.name !== 'AbortError') {
          setMessages((prev) => {
            const updated = [...prev]
            const last = updated[updated.length - 1]
            if (last.role === 'assistant' && !last.content) {
              updated[updated.length - 1] = {
                ...last,
                content: `Error: ${err.message}`,
              }
            }
            return updated
          })
        }
      } finally {
        setIsStreaming(false)
        abortRef.current = null

        setMessages((currentMessages) => {
          maybeCompact(currentMessages, model)
          return currentMessages
        })
      }
    },
    [messages, buildApiMessages, maybeCompact]
  )

  const stopStreaming = useCallback(() => {
    abortRef.current?.abort()
    setIsStreaming(false)
  }, [])

  const clearMessages = useCallback(() => {
    setMessages([])
    setMemoryContext(null)
  }, [])

  const tokenEstimate = estimateConversationTokens(messages)

  return {
    messages,
    isStreaming,
    isCompacting,
    systemPrompt,
    setSystemPrompt,
    resolvedPrompt,
    sendMessage,
    stopStreaming,
    clearMessages,
    memoryContext,
    tokenEstimate,
    // Preset management
    allPresets,
    activePresetName,
    selectPreset,
    savePreset,
    deletePreset,
  }
}
