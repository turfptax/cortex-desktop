import { useCallback, useEffect, useState } from 'react'
import { apiFetch } from '../../lib/api'

// ── Types ───────────────────────────────────────────────────────

interface Vitals {
  hunger: number
  cleanliness: number
  energy: number
  happiness: number
  intelligence: number
  is_coma: boolean
  is_sleeping?: boolean
  total_feeds: number
  total_cleans: number
}

interface TuckInResult {
  sleeping: boolean
  hub_ip: string | null
  hub_available: boolean
  new_interactions: number
  min_interactions: number
  interactions_ready: boolean
  cooldown_ok: boolean
  dream_ready: boolean
  error?: string
}

interface IntelligenceBreakdown {
  score: number
  components: Record<string, number>
}

interface ComaStatus {
  is_coma: boolean
  coma_id: number | null
  entered_at: string | null
  hours_in_coma: number | null
  revival_progress: Record<string, { current: number; target: number; met: boolean }>
}

interface VitalsSnapshot {
  hunger: number
  cleanliness: number
  energy: number
  happiness: number
  intelligence: number
  is_coma: boolean
  created_at: string
}

interface BadInteraction {
  id: number
  prompt: string
  response: string
  sentiment: number
  created_at: string
}

interface StageProgression {
  stage: number
  reached_at: string
  interactions: number
}

interface PetAnalytics {
  total_interactions: number
  current_mood_score: number
  stage_progression: StageProgression[]
  daily_trend: { date: string; avg_sentiment: number; count: number }[]
  performance: Record<string, number>
}

const STAGE_NAMES = ['Primordial', 'Babbling', 'Echoing', 'Responding', 'Conversing']
const STAGE_THRESHOLDS = [0, 50, 200, 1000, 5000]
const STAGE_COLORS = ['#868e96', '#ffd43b', '#51cf66', '#339af0', '#cc5de8']

// ── Helpers ─────────────────────────────────────────────────────

function VitalBar({
  label,
  value,
  color,
  lowColor,
  size = 'md',
}: {
  label: string
  value: number
  color: string
  lowColor: string
  size?: 'sm' | 'md'
}) {
  const pct = Math.min(100, Math.max(0, value * 100))
  const isLow = value < 0.3
  const isCritical = value < 0.15
  const h = size === 'sm' ? 'h-[4px]' : 'h-[8px]'

  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-text-muted w-[80px]">{label}</span>
      <div
        className={`flex-1 ${h} bg-surface-tertiary rounded-full overflow-hidden`}
      >
        <div
          className={`h-full rounded-full transition-all duration-500 ${
            isCritical ? 'animate-pulse' : ''
          }`}
          style={{
            width: `${pct}%`,
            backgroundColor: isLow ? lowColor : color,
          }}
        />
      </div>
      <span className="text-xs text-text-muted w-[36px] text-right tabular-nums">
        {Math.round(pct)}%
      </span>
    </div>
  )
}

function StatBox({
  label,
  value,
  sub,
}: {
  label: string
  value: string | number
  sub?: string
}) {
  return (
    <div className="bg-surface-tertiary rounded-lg px-3 py-2 text-center">
      <div className="text-lg font-bold text-text-primary">{value}</div>
      <div className="text-[10px] text-text-muted">{label}</div>
      {sub && <div className="text-[9px] text-text-muted mt-0.5">{sub}</div>}
    </div>
  )
}

function MiniChart({ history }: { history: VitalsSnapshot[] }) {
  if (history.length < 2) {
    return (
      <div className="text-xs text-text-muted text-center py-8">
        Not enough data for chart yet
      </div>
    )
  }

  const W = 600
  const H = 120
  const pad = { top: 10, bottom: 20, left: 0, right: 0 }
  const plotW = W - pad.left - pad.right
  const plotH = H - pad.top - pad.bottom

  const vitals: { key: keyof VitalsSnapshot; color: string; label: string; scale?: number }[] =
    [
      { key: 'hunger', color: '#ffa000', label: 'Hunger' },
      { key: 'cleanliness', color: '#00a0ff', label: 'Clean' },
      { key: 'energy', color: '#ff3c00', label: 'Energy' },
      { key: 'happiness', color: '#00c864', label: 'Happy' },
      { key: 'intelligence', color: '#c084fc', label: 'IQ', scale: 100 },
    ]

  const sorted = [...history].sort(
    (a, b) =>
      new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
  )
  const minT = new Date(sorted[0].created_at).getTime()
  const maxT = new Date(sorted[sorted.length - 1].created_at).getTime()
  const tRange = maxT - minT || 1

  function toPath(key: keyof VitalsSnapshot, scale = 1) {
    return sorted
      .map((s, i) => {
        const x =
          pad.left +
          ((new Date(s.created_at).getTime() - minT) / tRange) * plotW
        const raw = Number(s[key]) ?? 0
        const v = Math.min(raw / scale, 1)
        const y = pad.top + plotH - v * plotH
        return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`
      })
      .join(' ')
  }

  return (
    <div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        preserveAspectRatio="none"
      >
        {/* Grid lines */}
        {[0, 0.25, 0.5, 0.75, 1].map((v) => (
          <line
            key={v}
            x1={pad.left}
            x2={W - pad.right}
            y1={pad.top + plotH - v * plotH}
            y2={pad.top + plotH - v * plotH}
            stroke="var(--color-border)"
            strokeWidth={0.5}
            opacity={0.4}
          />
        ))}
        {vitals.map(({ key, color, scale }) => (
          <path
            key={key}
            d={toPath(key, scale)}
            fill="none"
            stroke={color}
            strokeWidth={2}
            opacity={0.8}
          />
        ))}
      </svg>
      <div className="flex gap-3 justify-center mt-1">
        {vitals.map(({ label, color }) => (
          <div key={label} className="flex items-center gap-1">
            <span
              className="w-2 h-2 rounded-full inline-block"
              style={{ backgroundColor: color }}
            />
            <span className="text-[10px] text-text-muted">{label}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Main Component ──────────────────────────────────────────────

interface Props {
  isOnline: boolean
}

export function PetCareTab({ isOnline }: Props) {
  const [vitals, setVitals] = useState<Vitals | null>(null)
  const [intelligence, setIntelligence] = useState<IntelligenceBreakdown | null>(null)
  const [comaStatus, setComaStatus] = useState<ComaStatus | null>(null)
  const [history, setHistory] = useState<VitalsSnapshot[]>([])
  const [badInteractions, setBadInteractions] = useState<BadInteraction[]>([])
  const [feedLoading, setFeedLoading] = useState(false)
  const [cleanLoading, setCleanLoading] = useState(false)
  const [restLoading, setRestLoading] = useState(false)
  const [lastRestResult, setLastRestResult] = useState<string | null>(null)
  const [historyHours, setHistoryHours] = useState(24)
  const [lastFeedResult, setLastFeedResult] = useState<string | null>(null)
  const [tuckInLoading, setTuckInLoading] = useState(false)
  const [tuckInResult, setTuckInResult] = useState<TuckInResult | null>(null)
  const [forceTrainLoading, setForceTrainLoading] = useState(false)
  const [forceTrainResult, setForceTrainResult] = useState<string | null>(null)
  const [wakeLoading, setWakeLoading] = useState(false)
  const [analytics, setAnalytics] = useState<PetAnalytics | null>(null)

  // ── Data fetching ─────────────────────────────────────────

  const fetchVitals = useCallback(async () => {
    try {
      const res = await apiFetch('/pi/pet/vitals')
      if (res?.data) setVitals(res.data)
    } catch { /* offline */ }
  }, [])

  const fetchIntelligence = useCallback(async () => {
    try {
      const res = await apiFetch('/pi/pet/intelligence')
      if (res?.data) setIntelligence(res.data)
    } catch { /* offline */ }
  }, [])

  const fetchComaStatus = useCallback(async () => {
    try {
      const res = await apiFetch('/pi/pet/coma-status')
      if (res?.data) setComaStatus(res.data)
    } catch { /* offline */ }
  }, [])

  const fetchHistory = useCallback(async () => {
    try {
      const res = await apiFetch(`/pi/pet/vitals-history?hours=${historyHours}`)
      // API returns { data: [...] } — the array is the data itself
      if (Array.isArray(res?.data)) setHistory(res.data)
      else if (res?.data?.history) setHistory(res.data.history)
    } catch { /* offline */ }
  }, [historyHours])

  const fetchAnalytics = useCallback(async () => {
    try {
      const res = await apiFetch('/pi/pet/analytics?days=365')
      const data = res?.data ?? res
      if (data && data.stage_progression) setAnalytics(data)
    } catch { /* offline */ }
  }, [])

  const fetchBadInteractions = useCallback(async () => {
    try {
      const res = await apiFetch('/pi/cmd', {
        method: 'POST',
        body: JSON.stringify({
          command: 'pet_clean',
          payload: { preview: true },
        }),
      })
      if (res?.data?.worst) setBadInteractions(res.data.worst)
    } catch { /* offline */ }
  }, [])

  // Initial fetch
  useEffect(() => {
    if (isOnline) {
      fetchVitals()
      fetchIntelligence()
      fetchComaStatus()
      fetchHistory()
      fetchBadInteractions()
      fetchAnalytics()
    }
  }, [isOnline, fetchVitals, fetchIntelligence, fetchComaStatus, fetchHistory, fetchBadInteractions, fetchAnalytics])

  // Auto-refresh vitals every 30s
  useEffect(() => {
    if (!isOnline) return
    const iv = setInterval(fetchVitals, 30_000)
    return () => clearInterval(iv)
  }, [isOnline, fetchVitals])

  // ── Actions ───────────────────────────────────────────────

  const handleFeed = async (type: string) => {
    setFeedLoading(true)
    setLastFeedResult(null)
    try {
      const res = await apiFetch('/pi/pet/feed', {
        method: 'POST',
        body: JSON.stringify({ type }),
      })
      const msg = res?.data?.message || 'Fed!'
      setLastFeedResult(msg)
      await fetchVitals()
    } catch (err: any) {
      setLastFeedResult(`Error: ${err.message}`)
    } finally {
      setFeedLoading(false)
      setTimeout(() => setLastFeedResult(null), 3000)
    }
  }

  const handleClean = async (discardIds: number[]) => {
    setCleanLoading(true)
    try {
      await apiFetch('/pi/pet/clean', {
        method: 'POST',
        body: JSON.stringify({ discard_ids: discardIds }),
      })
      await fetchVitals()
      setBadInteractions((prev) =>
        prev.filter((i) => !discardIds.includes(i.id))
      )
    } catch { /* offline */ }
    finally {
      setCleanLoading(false)
    }
  }

  const handleRest = async () => {
    setRestLoading(true)
    setLastRestResult(null)
    try {
      const res = await apiFetch('/pi/pet/rest', { method: 'POST' })
      const energy = res?.data?.energy
      setLastRestResult(energy != null ? `Energy: ${Math.round(energy * 100)}%` : 'Rested!')
      await fetchVitals()
    } catch (err: any) {
      setLastRestResult(`Error: ${err.message}`)
    } finally {
      setRestLoading(false)
      setTimeout(() => setLastRestResult(null), 3000)
    }
  }

  const handleTuckIn = async () => {
    setTuckInLoading(true)
    setTuckInResult(null)
    try {
      const res = await apiFetch('/pi/pet/tuck-in', { method: 'POST' })
      setTuckInResult(res?.data ?? { error: 'No response' })
      await fetchVitals()
    } catch (err: any) {
      setTuckInResult({ error: err.message } as TuckInResult)
    } finally {
      setTuckInLoading(false)
    }
  }

  const handleWake = async () => {
    setWakeLoading(true)
    try {
      await apiFetch('/pi/pet/wake', { method: 'POST' })
      setTuckInResult(null)
      await fetchVitals()
    } catch {
      // ignore
    } finally {
      setWakeLoading(false)
    }
  }

  const handleForceTrain = async () => {
    setForceTrainLoading(true)
    setForceTrainResult(null)
    try {
      const res = await apiFetch('/pi/pet/force-train', { method: 'POST' })
      setForceTrainResult(
        res?.data?.started ? 'Dream training started!' : 'Failed to start'
      )
    } catch (err: any) {
      setForceTrainResult(`Error: ${err.message}`)
    } finally {
      setForceTrainLoading(false)
      setTimeout(() => setForceTrainResult(null), 5000)
    }
  }

  // ── Render ────────────────────────────────────────────────

  if (!isOnline) {
    return (
      <div className="flex items-center justify-center h-full text-text-muted text-sm">
        Pi is offline
      </div>
    )
  }

  return (
    <div className="p-6 overflow-y-auto h-full space-y-6">
      {/* ── Vitals Dashboard ── */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-text-primary">
            Pet Vitals
          </h3>
          <button
            onClick={fetchVitals}
            className="text-xs text-accent hover:underline cursor-pointer"
          >
            Refresh
          </button>
        </div>

        {vitals ? (
          <div className="space-y-2">
            <VitalBar
              label="Hunger"
              value={vitals.hunger}
              color="#ffa000"
              lowColor="#ff5000"
            />
            <VitalBar
              label="Cleanliness"
              value={vitals.cleanliness}
              color="#00a0ff"
              lowColor="#785028"
            />
            <VitalBar
              label="Energy"
              value={vitals.energy}
              color="#ffdc00"
              lowColor="#ff3c00"
            />
            <VitalBar
              label="Happiness"
              value={vitals.happiness}
              color="#00c864"
              lowColor="#b46428"
            />

            {/* Stat boxes */}
            <div className="grid grid-cols-4 gap-2 mt-3">
              <StatBox
                label="IQ"
                value={Math.round(vitals.intelligence ?? 0)}
              />
              <StatBox label="Feeds" value={vitals.total_feeds ?? 0} />
              <StatBox label="Cleans" value={vitals.total_cleans ?? 0} />
              <StatBox
                label="Status"
                value={vitals.is_coma ? 'COMA' : vitals.is_sleeping ? 'Asleep' : 'Awake'}
              />
            </div>
          </div>
        ) : (
          <div className="text-xs text-text-muted">Loading vitals...</div>
        )}
      </section>

      {/* ── Coma Warning ── */}
      {comaStatus?.is_coma && (
        <section className="bg-red-500/10 border border-red-500/30 rounded-lg p-4">
          <h3 className="text-sm font-semibold text-red-400 mb-2">
            Pet is in a coma!
          </h3>
          <p className="text-xs text-text-secondary mb-3">
            Feed and clean your pet to revive it. All vitals must reach 30% for revival.
          </p>
          {comaStatus.revival_progress && (
            <div className="space-y-1">
              {Object.entries(comaStatus.revival_progress).map(
                ([key, prog]) => (
                  <div key={key} className="flex items-center gap-2 text-xs">
                    <span
                      className={`w-4 text-center ${
                        prog.met ? 'text-green-400' : 'text-red-400'
                      }`}
                    >
                      {prog.met ? '\u2713' : '\u2717'}
                    </span>
                    <span className="text-text-muted capitalize w-20">
                      {key}
                    </span>
                    <span className="text-text-secondary tabular-nums">
                      {Math.round(prog.current * 100)}% / {Math.round(prog.target * 100)}%
                    </span>
                  </div>
                )
              )}
            </div>
          )}
          {comaStatus.hours_in_coma != null && (
            <div className="text-[10px] text-text-muted mt-2">
              In coma for {comaStatus.hours_in_coma.toFixed(1)} hours
            </div>
          )}
        </section>
      )}

      {/* ── Feed & Clean Actions ── */}
      <section>
        <h3 className="text-sm font-semibold text-text-primary mb-3">
          Care Actions
        </h3>

        <div className="grid grid-cols-3 gap-2 mb-2">
          <button
            onClick={() => handleFeed('chat_snack')}
            disabled={feedLoading}
            className="bg-amber-500/15 hover:bg-amber-500/25 text-amber-300 rounded-lg px-3 py-2.5 text-xs font-medium transition-colors cursor-pointer disabled:opacity-50"
          >
            {feedLoading ? '...' : 'Chat Snack'}
            <div className="text-[9px] text-amber-400/60 mt-0.5">+15%</div>
          </button>
          <button
            onClick={() => handleFeed('data_meal')}
            disabled={feedLoading}
            className="bg-amber-500/15 hover:bg-amber-500/25 text-amber-300 rounded-lg px-3 py-2.5 text-xs font-medium transition-colors cursor-pointer disabled:opacity-50"
          >
            {feedLoading ? '...' : 'Data Meal'}
            <div className="text-[9px] text-amber-400/60 mt-0.5">+25%</div>
          </button>
          <button
            onClick={() => handleFeed('training_feast')}
            disabled={feedLoading}
            className="bg-amber-500/15 hover:bg-amber-500/25 text-amber-300 rounded-lg px-3 py-2.5 text-xs font-medium transition-colors cursor-pointer disabled:opacity-50"
          >
            {feedLoading ? '...' : 'Training Feast'}
            <div className="text-[9px] text-amber-400/60 mt-0.5">+40%</div>
          </button>
        </div>

        {lastFeedResult && (
          <div className="text-xs text-green-400 mb-2">{lastFeedResult}</div>
        )}

        {/* Rest button */}
        <div className="flex items-center gap-2 mb-2">
          <button
            onClick={handleRest}
            disabled={restLoading}
            className="bg-yellow-500/15 hover:bg-yellow-500/25 text-yellow-300 rounded-lg px-4 py-2 text-xs font-medium transition-colors cursor-pointer disabled:opacity-50"
          >
            {restLoading ? 'Resting...' : 'Rest'}
            <span className="text-[9px] text-yellow-400/60 ml-1.5">+10% energy</span>
          </button>
          {lastRestResult && (
            <span className="text-xs text-green-400">{lastRestResult}</span>
          )}
        </div>

        {/* Clean section */}
        {badInteractions.length > 0 ? (
          <div className="mt-3">
            <div className="text-xs text-text-muted mb-2">
              Bad interactions to clean ({badInteractions.length}):
            </div>
            <div className="space-y-1 max-h-[200px] overflow-y-auto">
              {badInteractions.map((bi) => (
                <div
                  key={bi.id}
                  className="flex items-center gap-2 bg-surface-tertiary rounded px-2 py-1.5"
                >
                  <div className="flex-1 min-w-0">
                    <div className="text-xs text-text-secondary truncate">
                      {bi.prompt}
                    </div>
                    <div className="text-[10px] text-red-400">
                      sentiment: {bi.sentiment?.toFixed(2)}
                    </div>
                  </div>
                  <button
                    onClick={() => handleClean([bi.id])}
                    disabled={cleanLoading}
                    className="text-[10px] text-blue-400 hover:text-blue-300 cursor-pointer whitespace-nowrap"
                  >
                    Discard
                  </button>
                </div>
              ))}
            </div>
          </div>
        ) : (
          <button
            onClick={() => handleClean([])}
            disabled={cleanLoading}
            className="bg-blue-500/15 hover:bg-blue-500/25 text-blue-300 rounded-lg px-4 py-2 text-xs font-medium transition-colors cursor-pointer disabled:opacity-50 mt-1"
          >
            {cleanLoading ? 'Cleaning...' : 'Quick Clean'}
          </button>
        )}
      </section>

      {/* ── Sleep & Training ── */}
      <section>
        <h3 className="text-sm font-semibold text-text-primary mb-3">
          Sleep & Training
        </h3>

        <div className="grid grid-cols-2 gap-2 mb-2">
          {vitals?.is_sleeping ? (
            <button
              onClick={handleWake}
              disabled={wakeLoading}
              className="bg-amber-500/15 hover:bg-amber-500/25 text-amber-300 rounded-lg px-3 py-2.5 text-xs font-medium transition-colors cursor-pointer disabled:opacity-50"
            >
              {wakeLoading ? 'Waking...' : 'Wake Up'}
              <div className="text-[9px] text-amber-400/60 mt-0.5">
                Wake from sleep
              </div>
            </button>
          ) : (
            <button
              onClick={handleTuckIn}
              disabled={tuckInLoading}
              className="bg-indigo-500/15 hover:bg-indigo-500/25 text-indigo-300 rounded-lg px-3 py-2.5 text-xs font-medium transition-colors cursor-pointer disabled:opacity-50"
            >
              {tuckInLoading ? 'Tucking in...' : 'Tuck In'}
              <div className="text-[9px] text-indigo-400/60 mt-0.5">
                Sleep + check readiness
              </div>
            </button>
          )}
          <button
            onClick={handleForceTrain}
            disabled={
              forceTrainLoading ||
              (tuckInResult != null &&
                !tuckInResult.error &&
                !tuckInResult.hub_available)
            }
            className="bg-purple-500/15 hover:bg-purple-500/25 text-purple-300 rounded-lg px-3 py-2.5 text-xs font-medium transition-colors cursor-pointer disabled:opacity-50"
          >
            {forceTrainLoading ? 'Starting...' : 'Force Train'}
            <div className="text-[9px] text-purple-400/60 mt-0.5">
              Bypass cooldown
            </div>
          </button>
        </div>

        {tuckInResult && !tuckInResult.error && (
          <div className="bg-surface-tertiary rounded-lg p-3 space-y-1.5 text-xs">
            <div className="flex items-center gap-2">
              <span className={tuckInResult.sleeping ? 'text-green-400' : 'text-red-400'}>
                {tuckInResult.sleeping ? '\u2713' : '\u2717'}
              </span>
              <span className="text-text-secondary">Pet is sleeping</span>
            </div>
            <div className="flex items-center gap-2">
              <span className={tuckInResult.hub_available ? 'text-green-400' : 'text-red-400'}>
                {tuckInResult.hub_available ? '\u2713' : '\u2717'}
              </span>
              <span className="text-text-secondary">
                Hub reachable{' '}
                {tuckInResult.hub_ip ? `(${tuckInResult.hub_ip})` : '(no IP)'}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <span
                className={
                  tuckInResult.interactions_ready
                    ? 'text-green-400'
                    : 'text-yellow-400'
                }
              >
                {tuckInResult.interactions_ready ? '\u2713' : '\u25cb'}
              </span>
              <span className="text-text-secondary">
                Interactions: {tuckInResult.new_interactions}/
                {tuckInResult.min_interactions}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <span
                className={
                  tuckInResult.cooldown_ok ? 'text-green-400' : 'text-yellow-400'
                }
              >
                {tuckInResult.cooldown_ok ? '\u2713' : '\u25cb'}
              </span>
              <span className="text-text-secondary">Cooldown clear</span>
            </div>
            {tuckInResult.dream_ready && (
              <div className="text-green-400 font-medium mt-1">
                Ready to dream!
              </div>
            )}
          </div>
        )}

        {tuckInResult?.error && (
          <div className="text-xs text-red-400">{tuckInResult.error}</div>
        )}

        {forceTrainResult && (
          <div className="text-xs text-purple-400 mt-2">{forceTrainResult}</div>
        )}
      </section>

      {/* ── Vitals History Chart ── */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-text-primary">
            Vitals History
          </h3>
          <div className="flex gap-1 flex-wrap">
            {([
              { hours: 1, label: '1h' },
              { hours: 2, label: '2h' },
              { hours: 4, label: '4h' },
              { hours: 8, label: '8h' },
              { hours: 16, label: '16h' },
              { hours: 24, label: '24h' },
              { hours: 48, label: '2d' },
              { hours: 168, label: '1w' },
              { hours: 730, label: '1mo' },
              { hours: 8760, label: '1y' },
            ] as const).map(({ hours, label }) => (
              <button
                key={hours}
                onClick={() => setHistoryHours(hours)}
                className={`text-[10px] px-2 py-0.5 rounded cursor-pointer ${
                  historyHours === hours
                    ? 'bg-accent text-white'
                    : 'text-text-muted hover:text-text-primary'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        <div className="bg-surface-tertiary rounded-lg p-3">
          <MiniChart history={history} />
        </div>
      </section>

      {/* ── Intelligence ── */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-text-primary">
            Intelligence
          </h3>
          <button
            onClick={fetchIntelligence}
            className="text-xs text-accent hover:underline cursor-pointer"
          >
            Refresh
          </button>
        </div>

        {intelligence ? (
          <div className="bg-surface-tertiary rounded-lg p-4">
            <div className="flex items-center gap-3 mb-3">
              <div className="text-3xl font-bold text-purple-400">
                {Math.round(intelligence.score)}
              </div>
              <div className="text-xs text-text-muted">IQ Score</div>
            </div>
            {intelligence.components && (
              <div className="space-y-1.5">
                {Object.entries(intelligence.components).map(([key, val]) => (
                  <div key={key} className="flex items-center gap-2">
                    <span className="text-[10px] text-text-muted w-[120px] capitalize">
                      {key.replace(/_/g, ' ')}
                    </span>
                    <div className="flex-1 h-[4px] bg-surface-secondary rounded-full overflow-hidden">
                      <div
                        className="h-full bg-purple-400/70 rounded-full"
                        style={{ width: `${Math.min(100, val)}%` }}
                      />
                    </div>
                    <span className="text-[10px] text-text-muted w-[28px] text-right tabular-nums">
                      {Math.round(val)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        ) : (
          <div className="text-xs text-text-muted">Loading intelligence...</div>
        )}
      </section>

      {/* ── Evolution / Blooms ── */}
      <section>
        <h3 className="text-sm font-semibold text-text-primary mb-3">
          Evolution
        </h3>

        {analytics ? (
          <div className="bg-surface-tertiary rounded-lg p-4 space-y-4">
            {/* Stage progress bar */}
            <div>
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs text-text-muted">
                  Total Interactions: <span className="text-text-primary font-medium">{analytics.total_interactions}</span>
                </span>
                <span className="text-xs text-text-muted">
                  Current Stage: <span className="font-medium" style={{ color: STAGE_COLORS[analytics.stage_progression.length - 1] || STAGE_COLORS[0] }}>
                    {STAGE_NAMES[analytics.stage_progression.length - 1] || STAGE_NAMES[0]}
                  </span>
                </span>
              </div>

              {/* Stage milestones */}
              <div className="relative h-8 bg-surface-secondary rounded-full overflow-hidden">
                {/* Fill bar based on progress to next stage */}
                {(() => {
                  const currentStage = analytics.stage_progression.length - 1
                  const nextThreshold = STAGE_THRESHOLDS[currentStage + 1] || STAGE_THRESHOLDS[STAGE_THRESHOLDS.length - 1]
                  const prevThreshold = STAGE_THRESHOLDS[currentStage] || 0
                  const progress = nextThreshold > prevThreshold
                    ? Math.min(1, (analytics.total_interactions - prevThreshold) / (nextThreshold - prevThreshold))
                    : 1
                  const totalProgress = currentStage >= STAGE_THRESHOLDS.length - 1
                    ? 100
                    : ((currentStage + progress) / (STAGE_THRESHOLDS.length - 1)) * 100
                  return (
                    <div
                      className="h-full rounded-full transition-all duration-1000"
                      style={{
                        width: `${totalProgress}%`,
                        background: `linear-gradient(90deg, ${STAGE_COLORS[0]}, ${STAGE_COLORS[Math.min(currentStage, STAGE_COLORS.length - 1)]})`,
                      }}
                    />
                  )
                })()}
                {/* Stage markers */}
                {STAGE_THRESHOLDS.slice(1).map((threshold, i) => {
                  const pct = ((i + 1) / (STAGE_THRESHOLDS.length - 1)) * 100
                  return (
                    <div
                      key={threshold}
                      className="absolute top-0 h-full w-px bg-border"
                      style={{ left: `${pct}%` }}
                    />
                  )
                })}
              </div>

              {/* Stage labels */}
              <div className="flex justify-between mt-1">
                {STAGE_NAMES.map((name, i) => {
                  const reached = i < analytics.stage_progression.length
                  return (
                    <span
                      key={name}
                      className="text-[9px] text-center"
                      style={{ color: reached ? STAGE_COLORS[i] : '#555' }}
                    >
                      {name}
                    </span>
                  )
                })}
              </div>
            </div>

            {/* Stage history timeline */}
            {analytics.stage_progression.length > 0 && (
              <div className="space-y-2">
                <h4 className="text-xs text-text-muted font-medium">Bloom History</h4>
                {analytics.stage_progression.map((sp, i) => (
                  <div key={sp.stage} className="flex items-center gap-3">
                    <div
                      className="w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold text-white shrink-0"
                      style={{ backgroundColor: STAGE_COLORS[sp.stage] || STAGE_COLORS[0] }}
                    >
                      {sp.stage + 1}
                    </div>
                    <div className="flex-1">
                      <div className="text-xs font-medium text-text-primary">
                        {STAGE_NAMES[sp.stage] || `Stage ${sp.stage}`}
                      </div>
                      <div className="text-[10px] text-text-muted">
                        {sp.reached_at ? new Date(sp.reached_at + 'Z').toLocaleDateString(undefined, {
                          month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit',
                        }) : 'Unknown'}
                        {' · '}
                        {sp.interactions} interaction{sp.interactions !== 1 ? 's' : ''} at this stage
                      </div>
                    </div>
                    {i < analytics.stage_progression.length - 1 && (
                      <span className="text-green-400 text-xs">Evolved</span>
                    )}
                    {i === analytics.stage_progression.length - 1 && (
                      <span className="text-accent text-xs">Current</span>
                    )}
                  </div>
                ))}

                {/* Next evolution hint */}
                {analytics.stage_progression.length < STAGE_NAMES.length && (
                  <div className="flex items-center gap-3 opacity-50">
                    <div className="w-6 h-6 rounded-full border-2 border-dashed border-text-muted flex items-center justify-center text-xs text-text-muted shrink-0">
                      ?
                    </div>
                    <div className="text-[10px] text-text-muted">
                      Next: <span className="font-medium">{STAGE_NAMES[analytics.stage_progression.length]}</span>
                      {' — needs '}
                      {STAGE_THRESHOLDS[analytics.stage_progression.length] - analytics.total_interactions} more interactions
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        ) : (
          <div className="text-xs text-text-muted">Loading evolution data...</div>
        )}
      </section>
    </div>
  )
}
