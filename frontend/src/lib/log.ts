// Tiny external store for the operation log, so plain functions (api client,
// polling loops outside React) can push entries and React panels can subscribe
// via useSyncExternalStore. Ported from addLog()/renderLogEntry() in app.js.

import { MAX_LOG_ENTRIES } from './constants'

export type LogLevel = 'info' | 'success' | 'warning' | 'error'

export interface LogEntry {
  level: LogLevel
  message: string
  ts: string
}

let entries: LogEntry[] = []
const listeners = new Set<() => void>()

function emit() {
  listeners.forEach((l) => l())
}

function nowTs(): string {
  const n = new Date()
  const p = (x: number) => String(x).padStart(2, '0')
  return `${p(n.getHours())}:${p(n.getMinutes())}:${p(n.getSeconds())}`
}

export function addLog(level: LogLevel, message: string): void {
  entries = [...entries, { level, message, ts: nowTs() }]
  if (entries.length > MAX_LOG_ENTRIES) entries = entries.slice(entries.length - MAX_LOG_ENTRIES)
  emit()
}

export function clearLogs(): void {
  entries = []
  addLog('info', '日志已清空')
}

export function getLogSnapshot(): LogEntry[] {
  return entries
}

function subscribe(cb: () => void): () => void {
  listeners.add(cb)
  return () => listeners.delete(cb)
}

// Re-export for useSyncExternalStore consumers.
export { subscribe }
