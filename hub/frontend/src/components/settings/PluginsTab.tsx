import { useEffect, useState } from 'react'
import { apiFetch } from '../../lib/api'
import {
  useInstalledPlugins,
  type InstalledPlugin,
  type MarketplacePlugin,
} from '../../hooks/useInstalledPlugins'

/** Settings → Plugins card.
 *
 * Phase 0 surface:
 *  - Installed list with status dot + version + variant + Restart/Uninstall
 *  - Marketplace list with Install button (501 stub — surfaces the
 *    documented hand-edit-registry.json flow as the workaround)
 *
 * Real install/update wiring lands in Phase 5 of the v0.18 cycle.
 */
export function PluginsTab() {
  const { plugins, loading, error, refresh } = useInstalledPlugins()
  const [marketplace, setMarketplace] = useState<MarketplacePlugin[]>([])
  const [busyId, setBusyId] = useState<string | null>(null)
  const [actionMessage, setActionMessage] = useState<string | null>(null)

  useEffect(() => {
    apiFetch<MarketplacePlugin[]>('/plugins/marketplace')
      .then(setMarketplace)
      .catch(() => setMarketplace([]))
  }, [plugins.length])

  const handleRestart = async (id: string) => {
    setBusyId(id)
    setActionMessage(null)
    try {
      await apiFetch(`/plugins/${id}/restart`, { method: 'POST' })
      setActionMessage(`Restarted ${id}`)
      await refresh()
    } catch (e) {
      setActionMessage(
        `Restart failed: ${e instanceof Error ? e.message : String(e)}`
      )
    }
    setBusyId(null)
  }

  const handleUninstall = async (id: string) => {
    if (!confirm(`Unregister ${id} from cortex-desktop? (Phase 0: registry-only — install_dir is left untouched.)`)) {
      return
    }
    setBusyId(id)
    setActionMessage(null)
    try {
      await apiFetch(`/plugins/${id}`, { method: 'DELETE' })
      setActionMessage(`Uninstalled ${id}`)
      await refresh()
    } catch (e) {
      setActionMessage(
        `Uninstall failed: ${e instanceof Error ? e.message : String(e)}`
      )
    }
    setBusyId(null)
  }

  const handleInstall = async (id: string) => {
    setBusyId(id)
    setActionMessage(null)
    try {
      await apiFetch('/plugins/install', {
        method: 'POST',
        body: JSON.stringify({ plugin_id: id }),
      })
      setActionMessage(`Installed ${id}`)
      await refresh()
    } catch (e) {
      // Expected: 501 in Phase 0
      setActionMessage(
        e instanceof Error ? e.message : String(e)
      )
    }
    setBusyId(null)
  }

  return (
    <div className="bg-surface rounded-xl p-5 border border-border">
      <div className="mb-4">
        <h2 className="text-base font-semibold text-text-primary">Plugins</h2>
        <p className="text-xs text-text-muted mt-0.5">
          Sidecar services that extend Cortex Hub. Each plugin runs as
          its own process and is proxied through the Hub.
        </p>
      </div>

      {loading && (
        <p className="text-sm text-text-muted">Loading plugins…</p>
      )}
      {error && !loading && (
        <p className="text-sm text-error">Could not load plugins: {error}</p>
      )}

      {/* Installed */}
      {!loading && (
        <div className="space-y-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-text-muted">
            Installed
          </h3>
          {plugins.length === 0 && (
            <p className="text-sm text-text-muted italic">
              No plugins installed yet.
            </p>
          )}
          {plugins.map((p) => (
            <PluginRow
              key={p.id}
              plugin={p}
              busy={busyId === p.id}
              onRestart={() => handleRestart(p.id)}
              onUninstall={() => handleUninstall(p.id)}
            />
          ))}
        </div>
      )}

      {/* Marketplace */}
      {marketplace.length > 0 && (
        <div className="space-y-3 mt-6 pt-6 border-t border-border">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-text-muted">
            Available
          </h3>
          {marketplace.map((m) => (
            <MarketplaceRow
              key={m.id}
              plugin={m}
              busy={busyId === m.id}
              onInstall={() => handleInstall(m.id)}
            />
          ))}
        </div>
      )}

      {actionMessage && (
        <div className="mt-4 text-xs text-text-secondary bg-surface-tertiary rounded-md p-3 whitespace-pre-wrap">
          {actionMessage}
        </div>
      )}
    </div>
  )
}

function PluginRow({
  plugin,
  busy,
  onRestart,
  onUninstall,
}: {
  plugin: InstalledPlugin
  busy: boolean
  onRestart: () => void
  onUninstall: () => void
}) {
  const dotColor = plugin.is_running ? 'bg-success' : 'bg-error'
  const status = plugin.is_running
    ? 'Running'
    : plugin.is_dev_mode
      ? 'Dev mode (sidecar offline)'
      : 'Stopped'
  const lastCheck = plugin.last_health_check
    ? new Date(plugin.last_health_check).toLocaleTimeString()
    : '—'

  return (
    <div className="bg-surface-secondary rounded-lg p-3 border border-border">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full ${dotColor}`} />
            <span className="text-sm font-semibold text-text-primary">
              {plugin.id}
            </span>
            <span className="text-xs text-text-muted">
              v{plugin.version} · {plugin.variant}
            </span>
            {plugin.is_dev_mode && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-accent/15 text-accent-hover">
                DEV
              </span>
            )}
          </div>
          <p className="text-xs text-text-muted mt-1">
            {status} · last health: {lastCheck} · {plugin.host}:{plugin.port}
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {!plugin.is_dev_mode && (
            <button
              onClick={onRestart}
              disabled={busy}
              className="px-2 py-1 text-xs rounded border border-border hover:bg-surface-tertiary disabled:opacity-50 cursor-pointer"
            >
              Restart
            </button>
          )}
          <button
            onClick={onUninstall}
            disabled={busy}
            className="px-2 py-1 text-xs rounded border border-border hover:bg-error/10 hover:text-error hover:border-error/40 disabled:opacity-50 cursor-pointer"
          >
            Uninstall
          </button>
        </div>
      </div>
    </div>
  )
}

function MarketplaceRow({
  plugin,
  busy,
  onInstall,
}: {
  plugin: MarketplacePlugin
  busy: boolean
  onInstall: () => void
}) {
  return (
    <div className="bg-surface-secondary rounded-lg p-3 border border-border">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-text-primary">
              {plugin.name}
            </span>
            <span className="text-xs text-text-muted">{plugin.id}</span>
          </div>
          <p className="text-xs text-text-secondary mt-1">{plugin.description}</p>
          <a
            href={`https://github.com/${plugin.github_repo}`}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-accent hover:text-accent-hover"
          >
            github.com/{plugin.github_repo}
          </a>
        </div>
        <div className="shrink-0">
          {plugin.installed ? (
            <span className="text-xs text-text-muted italic">Installed</span>
          ) : (
            <button
              onClick={onInstall}
              disabled={busy}
              className="px-3 py-1.5 text-xs rounded bg-accent/15 text-accent-hover hover:bg-accent/25 disabled:opacity-50 cursor-pointer"
            >
              Install
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
