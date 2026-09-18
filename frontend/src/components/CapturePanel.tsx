// Timetable capture via Playwright backend: start → poll /api/capture/status
// every 2s → stop. Ported from startCapture()/pollCapture()/stopCapture().
// The task's lifecycle is independent of this component being mounted; callers
// can re-open it to resume observing an in-flight capture.

import { useCallback, useEffect, useRef, useState } from 'react'
import { Button } from '@appica/ui-react/button'
import { Badge } from '@appica/ui-react/badge'
import { Progress } from '@appica/ui-react/progress'
import { api } from '../lib/api'
import { toast } from '../lib/toast'
import { CAPTURE_STEPS_MAP } from '../lib/constants'
import type { CaptureProgress } from '../lib/types'

interface Props {
  /** Fired once when a capture run finishes (success). */
  onFinished?: () => void
  autoFocusButton?: boolean
}

export function CapturePanel({ onFinished }: Props) {
  const [progress, setProgress] = useState<CaptureProgress | null>(null)
  const [running, setRunning] = useState(false)
  const pollRef = useRef<number | null>(null)

  const stopPolling = useCallback(() => {
    if (pollRef.current !== null) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }, [])

  const poll = useCallback(async () => {
    const res = await api<CaptureProgress>('/api/capture/status')
    setProgress(res)
    if (res.status === 'running') {
      setRunning(true)
      return
    }
    // any terminal state stops the loop
    stopPolling()
    setRunning(false)
    if (res.status === 'done') {
      toast(res.message || '捕获完成', 'success')
      onFinished?.()
    } else if (res.status === 'error') {
      toast(res.message || '捕获失败', 'error')
    }
  }, [onFinished, stopPolling])

  const start = useCallback(async () => {
    const res = await api<{ status: string; message?: string }>('/api/capture/start', { method: 'POST' })
    if (res.status !== 'ok') {
      toast(res.message || '无法启动捕获', 'error')
      return
    }
    setRunning(true)
    setProgress({ status: 'running', count: 0, message: '正在启动浏览器...' })
    stopPolling()
    pollRef.current = window.setInterval(poll, 2000)
  }, [poll, stopPolling])

  const requestStop = useCallback(async () => {
    await api('/api/capture/stop', { method: 'POST' })
  }, [])

  // Resume polling if a capture is already running when this mounts.
  useEffect(() => {
    ;(async () => {
      const res = await api<CaptureProgress>('/api/capture/status')
      if (res.status === 'running') {
        setRunning(true)
        setProgress(res)
        pollRef.current = window.setInterval(poll, 2000)
      }
    })()
    return stopPolling
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const stepIndex = (() => {
    if (!progress) return -1
    if (progress.status === 'done' || progress.status === 'error') return 3
    if ((progress.count || 0) > 0) return 2
    if (progress.message?.includes('页面已加载')) return 2
    if (progress.message?.includes('打开浏览器') || progress.message?.includes('启动')) return 0
    return 1
  })()

  return (
    <div className="flex flex-col gap-3">
      {!running ? (
        <Button className="w-full justify-center" onClick={start}>
          启动浏览器捕获课表
        </Button>
      ) : (
        <div className="flex flex-col gap-3 rounded-xl border border-border p-4">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium">课表捕获</span>
            <Badge variant="warning">进行中</Badge>
          </div>
          <Progress value={null} />
          <p className="text-xs text-foreground-muted">{progress?.message || '正在启动...'}</p>
          <div className="flex flex-wrap gap-2">
            {CAPTURE_STEPS_MAP.map((s, i) => (
              <span
                key={s.key}
                className={
                  'rounded-full px-2 py-0.5 text-xs ' +
                  (i < stepIndex
                    ? 'bg-success-soft text-success'
                    : i === stepIndex
                      ? 'bg-primary-soft text-primary'
                      : 'bg-background-muted text-foreground-muted')
                }
              >
                {s.label}
              </span>
            ))}
          </div>
          <Button variant="outline" className="w-full justify-center" onClick={requestStop}>
            停止捕获
          </Button>
        </div>
      )}
    </div>
  )
}
