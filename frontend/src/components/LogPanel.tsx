// Operation log side panel, ported from toggleLogPanel()/renderLogEntry().
import { useSyncExternalStore } from 'react'
import { Button } from '@appica/ui-react/button'
import { Dialog, DialogBody, DialogContent, DialogHeader, DialogTitle } from '@appica/ui-react/dialog'
import { getLogSnapshot, subscribe, clearLogs, type LogEntry } from '../lib/log'

const LEVEL_CLASS: Record<LogEntry['level'], string> = {
  info: 'text-foreground',
  success: 'text-green-600 dark:text-green-400',
  warning: 'text-orange-600 dark:text-orange-400',
  error: 'text-red-600 dark:text-red-400',
}

export function LogPanel({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const entries = useSyncExternalStore(subscribe, getLogSnapshot)
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>操作日志</DialogTitle>
        </DialogHeader>
        <DialogBody className="max-h-[60vh] overflow-y-auto">
          <div className="mb-2 flex justify-end">
            <Button size="sm" variant="outline" onClick={() => clearLogs()}>
              清空
            </Button>
          </div>
          <div className="flex flex-col gap-1 font-mono text-xs">
            {entries.map((e, i) => (
              <div key={i} className={LEVEL_CLASS[e.level]}>
                <span className="text-foreground-muted mr-2">{e.ts}</span>
                {e.message}
              </div>
            ))}
            {entries.length === 0 ? <div className="text-foreground-muted">（暂无日志）</div> : null}
          </div>
        </DialogBody>
      </DialogContent>
    </Dialog>
  )
}
