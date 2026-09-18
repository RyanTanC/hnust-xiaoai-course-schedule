// Imperative toast() callable from non-React code, backed by Appica's Toast
// (Base UI) external manager. Mirrors the original toast(msg, type) helper:
// auto-dismiss ~3s and a matching entry in the operation log.

import { ToastProvider, Toaster, createToastManager } from '@appica/ui-react/toast'
import { addLog, type LogLevel } from './log'

export const toastManager = createToastManager()

export type ToastType = 'error' | 'success' | 'warning' | undefined

export function toast(msg: string, type?: ToastType): void {
  toastManager.add({
    title: msg,
    timeout: type === 'error' ? 5000 : 3000,
  })
  const level: LogLevel =
    type === 'error' ? 'error' : type === 'success' ? 'success' : type === 'warning' ? 'warning' : 'info'
  addLog(level, msg)
}

/** Mount once near the app root: provides the manager + renders the stack. */
export function AppToaster() {
  return (
    <ToastProvider toastManager={toastManager} timeout={3000}>
      <Toaster position="bottom-center" timeout={3000} />
    </ToastProvider>
  )
}
