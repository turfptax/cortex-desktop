import { useState, useEffect } from 'react'
import { Layout } from './components/Layout'
import { ChatPage } from './components/chat/ChatPage'
import { PiPage } from './components/pi/PiPage'
import { DataPage } from './components/data/DataPage'
import { SettingsPage } from './components/settings/SettingsPage'
import { OverseerPage } from './components/overseer/OverseerPage'
import { VideoPlaceholder } from './components/video/VideoPlaceholder'
import { apiFetch } from './lib/api'
import { type PetStatus } from './components/PetWidget'
import { useInstalledPlugins } from './hooks/useInstalledPlugins'

export type Page = 'chat' | 'pi' | 'data' | 'overseer' | 'video' | 'settings'

export interface StatusInfo {
  lmstudioOnline: boolean
  piOnline: boolean
}

function App() {
  const [page, setPage] = useState<Page>('chat')
  const [status, setStatus] = useState<StatusInfo>({
    lmstudioOnline: false,
    piOnline: false,
  })
  const [petStatus, setPetStatus] = useState<PetStatus | null>(null)
  const { plugins } = useInstalledPlugins()
  const visionRunning =
    plugins.find((p) => p.id === 'cortex-vision')?.is_running ?? false

  // Deep-link guard: if user navigates to Video but the plugin isn't
  // running (e.g. plugin was uninstalled in another tab), bounce them
  // to Settings → Plugins. Half-broken pages age worse than missing
  // ones.
  useEffect(() => {
    if (page === 'video' && !visionRunning) {
      setPage('settings')
    }
  }, [page, visionRunning])

  // Poll connectivity status
  useEffect(() => {
    const checkStatus = async () => {
      try {
        const [chatHealth, piHealth, petHealth] = await Promise.allSettled([
          apiFetch<{ lmstudio_online: boolean }>('/chat/health'),
          apiFetch<{ online: boolean }>('/pi/online'),
          apiFetch<{ pet: PetStatus }>('/pi/pet/status'),
        ])

        const piIsOnline = piHealth.status === 'fulfilled' ? piHealth.value.online : false

        setStatus({
          lmstudioOnline:
            chatHealth.status === 'fulfilled'
              ? chatHealth.value.lmstudio_online
              : false,
          piOnline: piIsOnline,
        })

        setPetStatus(
          petHealth.status === 'fulfilled' && piIsOnline
            ? petHealth.value.pet
            : null
        )
      } catch {
        // Backend not running
      }
    }

    checkStatus()
    const interval = setInterval(checkStatus, 15000)
    return () => clearInterval(interval)
  }, [])

  return (
    <Layout
      page={page}
      setPage={setPage}
      status={status}
      petStatus={petStatus}
      visionRunning={visionRunning}
    >
      {page === 'chat' && <ChatPage petStatus={petStatus} />}
      {page === 'pi' && <PiPage status={status} />}
      {page === 'data' && <DataPage status={status} />}
      {page === 'overseer' && <OverseerPage />}
      {page === 'video' && visionRunning && <VideoPlaceholder />}
      {page === 'settings' && <SettingsPage />}
    </Layout>
  )
}

export default App
