import { useState } from 'react'
import { apiFetch } from '../../lib/api'

interface UpdateInfo {
  ok: boolean
  current_version: string
  latest_version: string
  update_available: boolean
  release_url?: string
  download_url?: string
  published_at?: string
  release_notes?: string
  message?: string
  error?: string
}

export function UpdateCard() {
  const [info, setInfo] = useState<UpdateInfo | null>(null)
  const [checking, setChecking] = useState(false)

  const handleCheck = async () => {
    setChecking(true)
    try {
      const res = await apiFetch<UpdateInfo>('/settings/check-update')
      setInfo(res)
    } catch {
      setInfo({ ok: false, error: 'Could not reach update server', current_version: '?', latest_version: '?', update_available: false })
    }
    setChecking(false)
  }

  return (
    <div className="bg-surface rounded-xl border border-border p-6">
      <h2 className="text-lg font-semibold text-text-primary mb-4 flex items-center gap-2">
        <span>🔄</span> Updates
      </h2>

      <div className="flex items-center gap-3 mb-4">
        <button
          onClick={handleCheck}
          disabled={checking}
          className="px-4 py-2 bg-surface-tertiary text-text-primary text-sm rounded-lg hover:bg-accent/15 hover:text-accent transition-colors disabled:opacity-50 cursor-pointer"
        >
          {checking ? 'Checking...' : 'Check for Updates'}
        </button>

        {info && !info.error && !info.update_available && (
          <span className="text-sm text-success">✅ You're up to date!</span>
        )}
      </div>

      {/* Update available banner */}
      {info?.update_available && (
        <div className="bg-accent/10 border border-accent/30 rounded-lg p-4 space-y-3">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium text-accent">
                Update available: v{info.latest_version}
              </p>
              <p className="text-xs text-text-muted mt-0.5">
                Current: v{info.current_version}
                {info.published_at && (
                  <> &middot; Released {new Date(info.published_at).toLocaleDateString()}</>
                )}
              </p>
            </div>
            <div className="flex gap-2">
              {info.download_url && (
                <a
                  href={info.download_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="px-3 py-1.5 bg-accent text-white text-xs rounded-lg hover:bg-accent-hover transition-colors"
                >
                  Download
                </a>
              )}
              {info.release_url && (
                <a
                  href={info.release_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="px-3 py-1.5 bg-surface-tertiary text-text-primary text-xs rounded-lg hover:bg-surface-secondary transition-colors"
                >
                  Release Notes
                </a>
              )}
            </div>
          </div>

          {info.release_notes && (
            <div className="text-xs text-text-secondary bg-surface-secondary rounded-lg p-3 max-h-32 overflow-y-auto whitespace-pre-wrap">
              {info.release_notes}
            </div>
          )}
        </div>
      )}

      {/* No releases yet */}
      {info && !info.error && !info.update_available && info.message && (
        <p className="text-xs text-text-muted">{info.message}</p>
      )}

      {/* Error */}
      {info?.error && (
        <p className="text-xs text-red-400">Failed to check: {info.error}</p>
      )}

      {/* Version footer */}
      <p className="text-xs text-text-muted mt-3">
        Current version: v{info?.current_version || '0.1.0'}
      </p>
    </div>
  )
}
