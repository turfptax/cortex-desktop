import { useState, useCallback } from 'react'
import { apiFetch } from '../../lib/api'

interface UpdateInfo {
  current_commit: string
  current_message: string
  latest_commit: string
  update_available: boolean
  changelog: string
}

interface ApplyResult {
  success: boolean
  old_commit: string
  new_commit: string
  pull_output: string
  service_restarted: boolean
}

interface Props {
  isOnline: boolean
}

export function FirmwareUpdate({ isOnline }: Props) {
  const [checking, setChecking] = useState(false)
  const [applying, setApplying] = useState(false)
  const [rollingBack, setRollingBack] = useState(false)
  const [updateInfo, setUpdateInfo] = useState<UpdateInfo | null>(null)
  const [applyResult, setApplyResult] = useState<ApplyResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  const checkForUpdates = useCallback(async () => {
    setChecking(true)
    setError(null)
    setApplyResult(null)
    try {
      const data = await apiFetch<UpdateInfo>('/pi/update/check')
      setUpdateInfo(data)
    } catch (err: any) {
      setError(err.message || 'Failed to check for updates')
    } finally {
      setChecking(false)
    }
  }, [])

  const applyUpdate = useCallback(async () => {
    setApplying(true)
    setError(null)
    try {
      const data = await apiFetch<ApplyResult>('/pi/update/apply', {
        method: 'POST',
        body: JSON.stringify({ restart_service: true }),
      })
      setApplyResult(data)
      setUpdateInfo(null)
    } catch (err: any) {
      setError(err.message || 'Failed to apply update')
    } finally {
      setApplying(false)
    }
  }, [])

  const rollback = useCallback(async () => {
    if (!confirm('Roll back to previous version? The pet service will restart.'))
      return
    setRollingBack(true)
    setError(null)
    try {
      const data = await apiFetch('/pi/update/rollback', {
        method: 'POST',
        body: JSON.stringify({}),
      })
      setApplyResult({
        success: data.success,
        old_commit: data.previous_commit,
        new_commit: data.rolled_back_to,
        pull_output: 'Rolled back',
        service_restarted: data.service_restarted,
      })
      setUpdateInfo(null)
    } catch (err: any) {
      setError(err.message || 'Rollback failed')
    } finally {
      setRollingBack(false)
    }
  }, [])

  if (!isOnline) {
    return (
      <div className="p-6 text-center text-text-muted">
        <p className="text-sm">Pi must be online to manage firmware.</p>
      </div>
    )
  }

  return (
    <div className="p-6 space-y-4 max-w-xl">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-text-primary">
          Pet Firmware
        </h3>
        <div className="flex gap-2">
          <button
            onClick={checkForUpdates}
            disabled={checking}
            className="px-3 py-1.5 rounded-md text-xs font-medium bg-accent text-white hover:bg-accent-hover transition-colors cursor-pointer disabled:opacity-50"
          >
            {checking ? 'Checking...' : 'Check for Updates'}
          </button>
          <button
            onClick={rollback}
            disabled={rollingBack || applying}
            className="px-3 py-1.5 rounded-md text-xs font-medium bg-surface-tertiary text-text-secondary hover:text-text-primary transition-colors cursor-pointer disabled:opacity-50"
          >
            {rollingBack ? 'Rolling back...' : 'Rollback'}
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-error/10 border border-error/30 rounded-lg p-3">
          <p className="text-xs text-error">{error}</p>
        </div>
      )}

      {updateInfo && (
        <div className="bg-surface-secondary rounded-xl p-5 border border-border space-y-3">
          <div className="flex items-center gap-2">
            <span
              className={`w-2 h-2 rounded-full ${
                updateInfo.update_available ? 'bg-warning' : 'bg-success'
              }`}
            />
            <span className="text-sm font-medium text-text-primary">
              {updateInfo.update_available
                ? 'Update Available'
                : 'Up to Date'}
            </span>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="bg-surface rounded-lg p-3 border border-border">
              <p className="text-xs text-text-muted mb-1">Current</p>
              <p className="text-sm font-mono text-text-primary">
                {updateInfo.current_commit}
              </p>
              <p className="text-xs text-text-muted truncate mt-1">
                {updateInfo.current_message}
              </p>
            </div>
            <div className="bg-surface rounded-lg p-3 border border-border">
              <p className="text-xs text-text-muted mb-1">Latest</p>
              <p className="text-sm font-mono text-text-primary">
                {updateInfo.latest_commit}
              </p>
            </div>
          </div>

          {updateInfo.changelog && (
            <div>
              <p className="text-xs text-text-muted mb-1">Changelog</p>
              <pre className="text-xs font-mono text-text-secondary bg-surface rounded-lg p-3 border border-border whitespace-pre-wrap">
                {updateInfo.changelog}
              </pre>
            </div>
          )}

          {updateInfo.update_available && (
            <button
              onClick={applyUpdate}
              disabled={applying}
              className="w-full px-4 py-2.5 rounded-lg bg-accent text-white text-sm font-medium hover:bg-accent-hover transition-colors cursor-pointer disabled:opacity-50"
            >
              {applying
                ? 'Updating... (this takes ~30s)'
                : 'Update Pet Firmware'}
            </button>
          )}
        </div>
      )}

      {applyResult && (
        <div
          className={`rounded-xl p-5 border space-y-2 ${
            applyResult.success
              ? 'bg-success/10 border-success/30'
              : 'bg-error/10 border-error/30'
          }`}
        >
          <p className="text-sm font-medium text-text-primary">
            {applyResult.success ? 'Update Applied' : 'Update Failed'}
          </p>
          <p className="text-xs text-text-secondary">
            {applyResult.old_commit} → {applyResult.new_commit}
          </p>
          {applyResult.service_restarted && (
            <p className="text-xs text-success">Service restarted</p>
          )}
          {applyResult.pull_output && (
            <pre className="text-xs font-mono text-text-muted bg-surface rounded p-2 whitespace-pre-wrap">
              {applyResult.pull_output}
            </pre>
          )}
        </div>
      )}

      {!updateInfo && !applyResult && !error && (
        <div className="bg-surface-secondary rounded-xl p-6 border border-border text-center">
          <p className="text-sm text-text-muted">
            Click "Check for Updates" to see if new firmware is available for
            your pet.
          </p>
          <p className="text-xs text-text-muted mt-2">
            Updates are pulled from GitHub and applied via SSH. The pet service
            restarts automatically.
          </p>
        </div>
      )}
    </div>
  )
}
