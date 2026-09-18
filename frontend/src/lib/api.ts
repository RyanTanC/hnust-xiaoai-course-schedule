// Central fetch wrapper ported from app.js api(): attaches X-Auth-Token, logs
// each call, and funnels every `code:'unauthorized'` through a single handler
// (registered by the app store) to avoid a redirect storm on 401s.

import { AUTH_TOKEN_KEY } from './constants'
import { addLog } from './log'

type UnauthorizedHandler = () => void
let onUnauthorized: UnauthorizedHandler | null = null
let redirecting = false

export function setUnauthorizedHandler(fn: UnauthorizedHandler): void {
  onUnauthorized = fn
}

export interface ApiOptions extends Omit<RequestInit, 'headers'> {
  headers?: Record<string, string>
}

export async function api<T = Record<string, any>>(path: string, opts?: ApiOptions): Promise<T> {
  const startTime = Date.now()
  const method = (opts && opts.method) || 'GET'
  const savedToken = localStorage.getItem(AUTH_TOKEN_KEY)
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    Accept: 'application/json',
  }
  if (savedToken) headers['X-Auth-Token'] = savedToken
  const { headers: extraHeaders, ...restOpts } = opts || {}
  addLog('info', `${method} ${path}`)
  try {
    const res = await fetch(path, { ...restOpts, headers: { ...headers, ...(extraHeaders || {}) } })
    const data: any = await res.json()
    const elapsed = Date.now() - startTime
    // Some endpoints (e.g. /api/config) return a bare object with no `status`
    // field; treat an HTTP-successful response without an explicit failure as OK.
    if (data.status === 'ok' || (data.status === undefined && res.ok)) {
      addLog('success', `${method} ${path} [${elapsed}ms] OK`)
    } else {
      addLog('warning', `${method} ${path} [${elapsed}ms] ${data.message || 'failed'}`)
    }

    if (data.code === 'unauthorized' && !redirecting) {
      redirecting = true
      localStorage.removeItem(AUTH_TOKEN_KEY)
      onUnauthorized?.()
      setTimeout(() => {
        redirecting = false
      }, 3000)
    }
    return data as T
  } catch (e) {
    const elapsed = Date.now() - startTime
    const msg = e instanceof Error ? e.message : String(e)
    addLog('error', `${method} ${path} [${elapsed}ms] Network Error: ${msg}`)
    return { status: 'error', message: `网络错误: ${msg}` } as T
  }
}
