// App-level shared state: config, auth token, cloud table list, and the current
// top-level page. Also wires the global 401 -> login redirect. Feature-specific
// polling (capture / sync) is owned by its component, not here.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { api, setUnauthorizedHandler } from './api'
import { toast } from './toast'
import { addLog } from './log'
import { AUTH_TOKEN_KEY, CACHE_KEY } from './constants'
import type { AppConfig, TableSummary } from './types'

export type Page = 'login' | 'dashboard'

interface AppState {
  booting: boolean
  page: Page
  setPage: (p: Page) => void
  config: AppConfig | null
  token: string | null
  tables: TableSummary[]
  setTables: (t: TableSummary[]) => void
  refreshConfig: () => Promise<void>
  /** Persist a fresh token (dual-write) + table list. Set navigate=false to keep the
   *  caller (e.g. the setup wizard) in control of which page shows next. */
  completeLogin: (token: string | undefined, tables: TableSummary[] | undefined, msg: string, navigate?: boolean) => void
  logout: () => void
  refreshTables: () => Promise<void>
  /** Clears the booting gate once the initial cached-session check resolves. */
  endBoot: () => void
}

const Ctx = createContext<AppState | null>(null)

export function useApp(): AppState {
  const v = useContext(Ctx)
  if (!v) throw new Error('useApp must be used within AppProvider')
  return v
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [booting, setBooting] = useState(true)
  const [page, setPage] = useState<Page>('login')
  const [config, setConfig] = useState<AppConfig | null>(null)
  const [token, setToken] = useState<string | null>(null)
  const [tables, setTables] = useState<TableSummary[]>([])
  const pageRef = useRef(page)
  pageRef.current = page

  const refreshConfig = useCallback(async () => {
    const cfg = await api<AppConfig>('/api/config')
    setConfig(cfg)
  }, [])

  const completeLogin = useCallback(
    (tk: string | undefined, tbs: TableSummary[] | undefined, msg: string, navigate = true) => {
      if (tk) {
        localStorage.setItem(AUTH_TOKEN_KEY, tk)
        setToken(tk)
      }
      const list = tbs || []
      setTables(list)
      try {
        localStorage.setItem(CACHE_KEY, JSON.stringify(list))
        sessionStorage.setItem('tables', JSON.stringify(list))
      } catch {
        /* storage quota / private mode — non-fatal */
      }
      if (navigate) setPage('dashboard')
      toast(msg, 'success')
    },
    [],
  )

  const logout = useCallback(() => {
    localStorage.removeItem(AUTH_TOKEN_KEY)
    setToken(null)
    setTables([])
    setPage('login')
    toast('已退出登录')
    addLog('info', 'User logged out')
  }, [])

  const refreshTables = useCallback(async () => {
    const res = await api<{ status: string; tables?: TableSummary[]; message?: string }>('/api/tables')
    if (res.status === 'ok' && res.tables) {
      setTables(res.tables)
      try {
        localStorage.setItem(CACHE_KEY, JSON.stringify(res.tables))
        sessionStorage.setItem('tables', JSON.stringify(res.tables))
      } catch {
        /* ignore */
      }
    }
  }, [])

  // Global 401 handler: bounce to login with a warning (matches app.js behavior).
  useEffect(() => {
    setUnauthorizedHandler(() => {
      setToken(null)
      setTables([])
      setPage('login')
      toast('登录凭证已失效，请重新登录（推荐扫码登录）', 'warning')
      addLog('warning', 'Auth expired: redirected to login page')
    })
  }, [])

  const endBoot = useCallback(() => setBooting(false), [])

  const value = useMemo<AppState>(
    () => ({
      booting,
      page,
      setPage,
      config,
      token,
      tables,
      setTables,
      refreshConfig,
      completeLogin,
      logout,
      refreshTables,
      endBoot,
    }),
    [booting, page, config, token, tables, refreshConfig, completeLogin, logout, refreshTables, endBoot],
  )

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}
