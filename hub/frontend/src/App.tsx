import { useState, useEffect } from 'react'
import { Layout } from './components/Layout'
import { ChatPage } from './components/chat/ChatPage'
import { TrainingPage } from './components/training/TrainingPage'
import { PiPage } from './components/pi/PiPage'
import { GamesPage } from './components/games/GamesPage'
import { DataPage } from './components/data/DataPage'
import { SettingsPage } from './components/settings/SettingsPage'
import { apiFetch } from './lib/api'
import { type PetStatus } from './components/PetWidget'

export type Page = 'chat' | 'training' | 'pi' | 'games' | 'data' | 'settings'

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
    <Layout page={page} setPage={setPage} status={status} petStatus={petStatus}>
      {page === 'chat' && <ChatPage petStatus={petStatus} />}
      {page === 'training' && <TrainingPage />}
      {page === 'pi' && <PiPage status={status} />}
      {page === 'games' && <GamesPage />}
      {page === 'data' && <DataPage status={status} />}
      {page === 'settings' && <SettingsPage />}
    </Layout>
  )
}

export default App
