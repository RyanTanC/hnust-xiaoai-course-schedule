// App shell: boot (cached-token auto-login) + top-level routing between the
// login wizard and the dashboard, wrapped by the shared Navbar / toaster /
// action-sheet host / log panel. Rebuilt from the DOMContentLoaded boot() and
// navbar wiring in app.js.

import { useCallback, useEffect, useState } from 'react'
import { Button } from '@appica/ui-react/button'
import { Spinner } from '@appica/ui-react/spinner'
import { api } from './lib/api'
import { useApp } from './lib/store'
import { AUTH_TOKEN_KEY, CACHE_KEY } from './lib/constants'
import { AppToaster } from './lib/toast'
import { ActionSheetHost } from './lib/actionSheet'
import { LogPanel } from './components/LogPanel'
import { LoginPage } from './pages/LoginPage'
import { DashboardPage } from './pages/DashboardPage'
import type { TableSummary } from './lib/types'

function Shell() {
  const app = useApp()
  const [logOpen, setLogOpen] = useState(false)

  // Boot: restore config, then try to resume a cached session.
  const boot = useCallback(async () => {
    await app.refreshConfig()
    const saved = localStorage.getItem(AUTH_TOKEN_KEY)
    if (saved) {
      const res = await api<{ status: string; tables?: TableSummary[] }>('/api/tables')
      if (res.status === 'ok' && res.tables) {
        app.setTables(res.tables)
        try {
          localStorage.setItem(CACHE_KEY, JSON.stringify(res.tables))
        } catch {
          /* ignore */
        }
        app.setPage('dashboard')
      }
    } else {
      // No live token: fall back to the last known table list for offline browse.
      try {
        const cached = localStorage.getItem(CACHE_KEY)
        if (cached) app.setTables(JSON.parse(cached) as TableSummary[])
      } catch {
        /* ignore */
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    boot().finally(() => app.endBoot())
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [boot])

  if (app.booting) {
    return (
      <div className="flex min-h-screen items-center justify-center gap-2 text-sm text-foreground-muted">
        <Spinner className="size-5" /> 正在检查登录状态...
      </div>
    )
  }

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-10 flex items-center justify-between gap-2 border-b border-border bg-background/80 px-4 py-3 backdrop-blur">
        <span className="text-base font-semibold">小爱课程表 · HNUST</span>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="ghost" onClick={() => setLogOpen(true)}>
            日志
          </Button>
          {app.page === 'dashboard' ? (
            <Button size="sm" variant="outline" onClick={() => app.logout()}>
              退出登录
            </Button>
          ) : null}
        </div>
      </header>

      <main className="flex-1">
        {app.page === 'dashboard' ? <DashboardPage /> : <LoginPage />}
      </main>

      <LogPanel open={logOpen} onOpenChange={setLogOpen} />
    </div>
  )
}

export default function App() {
  return (
    <>
      <Shell />
      <AppToaster />
      <ActionSheetHost />
    </>
  )
}
