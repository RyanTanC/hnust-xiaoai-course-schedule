// Settings modal: debug-mode toggle (POST /api/debug-mode) and connectivity
// diagnostics (GET /api/diagnose). Ported from showSettings()/toggleDebug()/runDiagnose().

import { useCallback, useState } from 'react'
import { Button } from '@appica/ui-react/button'
import { Switch } from '@appica/ui-react/switch'
import { Badge } from '@appica/ui-react/badge'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@appica/ui-react/dialog'
import { api } from '../lib/api'
import { toast } from '../lib/toast'

interface Props {
  open: boolean
  onOpenChange: (o: boolean) => void
  debugMode: boolean
  onDebugChange: (v: boolean) => void
}

type Diag = Record<string, any> | null

export function SettingsDialog({ open, onOpenChange, debugMode, onDebugChange }: Props) {
  const [busy, setBusy] = useState(false)
  const [diag, setDiag] = useState<Diag>(null)

  const toggleDebug = useCallback(
    async (v: boolean) => {
      const res = await api<{ status: string; debug_mode?: boolean; message?: string }>('/api/debug-mode', {
        method: 'POST',
        body: JSON.stringify({ debug_mode: v }),
      })
      if (res.status === 'ok') {
        onDebugChange(!!res.debug_mode)
        toast(res.debug_mode ? '已开启调试模式' : '已关闭调试模式', 'success')
      } else {
        toast(res.message || '切换失败', 'error')
      }
    },
    [onDebugChange],
  )

  const runDiagnose = useCallback(async () => {
    setBusy(true)
    const res = await api<Diag>('/api/diagnose')
    setDiag(res)
    setBusy(false)
  }, [])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>设置</DialogTitle>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium">调试模式</p>
              <p className="text-xs text-foreground-muted">开启后接口会输出更详细的请求日志。</p>
            </div>
            <Switch checked={debugMode} onCheckedChange={toggleDebug} />
          </div>

          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <p className="text-sm font-medium">连通性诊断</p>
              <Button size="sm" variant="outline" onClick={runDiagnose} disabled={busy}>
                {busy ? '诊断中...' : '开始诊断'}
              </Button>
            </div>
            {diag ? <DiagView diag={diag} /> : null}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            关闭
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function DiagView({ diag }: { diag: NonNullable<Diag> }) {
  const rows: [string, any][] = [
    ['小米账号', diag.xiaomi_account],
    ['AI 服务', diag.ai_service],
    ['DNS', diag.dns],
  ]
  return (
    <div className="flex flex-col gap-1 rounded-lg border border-border p-2 text-xs">
      {rows.map(([label, v]) => (
        <div key={label} className="flex items-center justify-between">
          <span className="text-foreground-muted">{label}</span>
          <Badge variant={v?.ok ? 'success' : 'error'}>{v?.ok ? `正常 ${v.status ?? v.ip ?? ''}` : '异常'}</Badge>
        </div>
      ))}
      <div className="mt-1 flex items-center justify-between border-t border-border pt-1">
        <span className="text-foreground-muted">整体</span>
        <Badge variant={diag.overall === 'ok' ? 'success' : 'warning'}>{diag.overall}</Badge>
      </div>
    </div>
  )
}
