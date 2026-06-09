import { useState, useEffect, useCallback } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Layout } from './components/Layout'
import { ChatPage } from './components/chat/ChatPage'
import { PiPage } from './components/pi/PiPage'
import { DataPage } from './components/data/DataPage'
import { SettingsPage } from './components/settings/SettingsPage'
import { OverseerPage } from './components/overseer/OverseerPage'
import { VideoPage } from './components/video/VideoPage'
import { apiFetch } from './lib/api'
import { type PetStatus } from './components/PetWidget'
import { useInstalledPlugins } from './hooks/useInstalledPlugins'

export type Page = 'chat' | 'pi' | 'data' | 'overseer' | 'video' | 'settings'

const PAGES: readonly Page[] = [
  'chat', 'pi', 'data', 'overseer', 'video', 'settings',
]

export interface StatusInfo {
  lmstudioOnline: boolean
  piOnline: boolean
}

/** The URL hash is the source of truth for the top-level tab, so
 * tabs survive a refresh and can be deep-linked / bookmarked
 * (e.g. http://localhost:8003/#/overseer). */
function pageFromHash(): Page {
  const h = window.location.hash.replace(/^#\/?/, '')
  return (PAGES as readonly string[]).includes(h) ? (h as Page) : 'chat'
}

function useHashPage(): [Page, (p: Page) => void] {
  const [page, setPageState] = useState<Page>(pageFromHash)

  useEffect(() => {
    const onHashChange = () => setPageState(pageFromHash())
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  const setPage = useCallback((p: Page) => {
    window.location.hash = `/${p}`
  }, [])

  return [page, setPage]
}

interface HealthSnapshot {
  status: StatusInfo
  petStatus: PetStatus | null
}

async function fetchHealthSnapshot(): Promise<HealthSnapshot> {
  const [chatHealth, piHealth, petHealth] = await Promise.allSettled([
    apiFetch<{ lmstudio_online: boolean }>('/chat/health'),
    apiFetch<{ online: boolean }>('/pi/online'),
    apiFetch<{ pet: PetStatus }>('/pi/pet/status'),
  ])

  const piIsOnline =
    piHealth.status === 'fulfilled' ? piHealth.value.online : false

  return {
    status: {
      lmstudioOnline:
        chatHealth.status === 'fulfilled'
          ? chatHealth.value.lmstudio_online
          : false,
      piOnline: piIsOnline,
    },
    petStatus:
      petHealth.status === 'fulfilled' && piIsOnline
        ? petHealth.value.pet
        : null,
  }
}

function App() {
  const [page, setPage] = useHashPage()

  // Connectivity polling through the shared query cache (was a
  // hand-rolled setInterval). refetchInterval keeps the 15s cadence;
  // any other component can read the same snapshot via its queryKey
  // without firing a second request.
  const { data: health } = useQuery({
    queryKey: ['health-snapshot'],
    queryFn: fetchHealthSnapshot,
    refetchInterval: 15_000,
  })
  const status = health?.status ?? { lmstudioOnline: false, piOnline: false }
  const petStatus = health?.petStatus ?? null

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
  }, [page, visionRunning, setPage])

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
      {page === 'video' && visionRunning && <VideoPage />}
      {page === 'settings' && <SettingsPage />}
    </Layout>
  )
}

export default App
