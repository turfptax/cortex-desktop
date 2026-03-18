import { type PiStatus } from '../../hooks/usePi'

interface Props {
  piStatus: PiStatus | null
  onRefresh: () => void
}

export function StatusCard({ piStatus, onRefresh }: Props) {
  if (!piStatus) {
    return (
      <div className="text-center py-8 text-text-muted">
        <p className="text-3xl mb-2">🥧</p>
        <p className="text-sm">Loading Pi status...</p>
      </div>
    )
  }

  if (!piStatus.online) {
    return (
      <div className="bg-surface-secondary rounded-xl p-6 border border-border text-center">
        <p className="text-3xl mb-3">📡</p>
        <p className="text-sm text-text-muted mb-4">
          Pi Zero is offline or unreachable
        </p>
        <p className="text-xs text-text-muted mb-4">
          {piStatus.error || 'Check network connection'}
        </p>
        <button
          onClick={onRefresh}
          className="px-4 py-2 rounded-lg bg-accent text-white text-xs font-medium hover:bg-accent-hover transition-colors cursor-pointer"
        >
          Retry
        </button>
      </div>
    )
  }

  const health = piStatus.health || {}
  const status = piStatus.status || {}
  const data = status.result || status.data || status

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-text-primary">
          System Status
        </h3>
        <button
          onClick={onRefresh}
          className="px-3 py-1.5 rounded-md text-xs font-medium bg-surface-tertiary text-text-secondary hover:text-text-primary transition-colors cursor-pointer"
        >
          Refresh
        </button>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
        {health.hostname && (
          <InfoCard label="Hostname" value={health.hostname} />
        )}
        {data.uptime && <InfoCard label="Uptime" value={data.uptime} />}
        {data.ip_address && (
          <InfoCard label="IP Address" value={data.ip_address} />
        )}
        {data.wifi_ssid && (
          <InfoCard label="WiFi" value={data.wifi_ssid} />
        )}
        {data.storage_used && (
          <InfoCard
            label="Storage"
            value={`${data.storage_used} / ${data.storage_total}`}
          />
        )}
        {data.cpu_temp && (
          <InfoCard label="CPU Temp" value={data.cpu_temp} />
        )}
      </div>

      {/* Battery */}
      {data.battery && (
        <BatteryCard battery={data.battery} />
      )}

      {/* Pet info */}
      {data.pet && (
        <div className="bg-surface-secondary rounded-xl p-5 border border-border">
          <h4 className="text-xs font-medium text-text-muted uppercase mb-3">
            Pet Status
          </h4>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {data.pet.stage && (
              <InfoCard label="Stage" value={`${data.pet.stage}`} />
            )}
            {data.pet.mood && (
              <InfoCard label="Mood" value={data.pet.mood} />
            )}
            {data.pet.xp !== undefined && (
              <InfoCard label="XP" value={`${data.pet.xp}`} />
            )}
            {data.pet.interactions !== undefined && (
              <InfoCard
                label="Interactions"
                value={`${data.pet.interactions}`}
              />
            )}
          </div>
        </div>
      )}

      {/* Raw data toggle */}
      <details className="bg-surface-secondary rounded-xl border border-border">
        <summary className="px-4 py-3 text-xs text-text-muted cursor-pointer hover:text-text-secondary">
          Raw status data
        </summary>
        <pre className="px-4 pb-4 text-xs text-text-muted overflow-x-auto font-mono">
          {JSON.stringify(piStatus, null, 2)}
        </pre>
      </details>
    </div>
  )
}

function InfoCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-surface rounded-lg p-3 border border-border">
      <p className="text-xs text-text-muted mb-1">{label}</p>
      <p className="text-sm font-medium text-text-primary truncate">
        {value}
      </p>
    </div>
  )
}

function BatteryCard({ battery }: { battery: { percentage?: number; charging?: boolean; voltage_v?: number; temperature_c?: number } }) {
  const pct = battery.percentage ?? 0
  const charging = battery.charging ?? false
  const color = pct > 50 ? '#51cf66' : pct > 20 ? '#ffd43b' : '#ff6b6b'
  const icon = charging ? '⚡' : pct > 75 ? '🔋' : pct > 20 ? '🪫' : '🪫'

  return (
    <div className="bg-surface-secondary rounded-xl p-5 border border-border">
      <h4 className="text-xs font-medium text-text-muted uppercase mb-3">
        Battery
      </h4>
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2">
          <span className="text-2xl">{icon}</span>
          <div>
            <p className="text-2xl font-bold text-text-primary">
              {Math.round(pct)}%
              {charging && <span className="text-sm text-success ml-1">Charging</span>}
            </p>
          </div>
        </div>
        {/* Battery bar */}
        <div className="flex-1">
          <div className="h-6 bg-surface-tertiary rounded-full overflow-hidden border border-border">
            <div
              className="h-full rounded-full transition-all duration-500"
              style={{ width: `${pct}%`, backgroundColor: color }}
            />
          </div>
        </div>
      </div>
      <div className="flex gap-4 mt-2 text-xs text-text-muted">
        {battery.voltage_v !== undefined && (
          <span>Voltage: {battery.voltage_v.toFixed(2)}V</span>
        )}
        {battery.temperature_c !== undefined && (
          <span>Temp: {battery.temperature_c.toFixed(1)}°C</span>
        )}
      </div>
    </div>
  )
}
