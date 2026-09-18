// Promise-based action sheet, ported from app.js showActionSheet(): resolves the
// chosen action index, or -1 on cancel / backdrop. Renders with Appica Dialog.

import { useSyncExternalStore } from 'react'
import { Button } from '@appica/ui-react/button'
import {
  Dialog,
  DialogContent,
  DialogBody,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogClose,
} from '@appica/ui-react/dialog'

export interface ActionSheetAction {
  label: string
  destructive?: boolean
}
export interface ActionSheetOptions {
  title?: string
  message?: string
  actions?: ActionSheetAction[]
}

interface SheetState {
  open: boolean
  options: ActionSheetOptions
}

let state: SheetState = { open: false, options: {} }
let resolver: ((idx: number) => void) | null = null
const listeners = new Set<() => void>()
function emit() {
  listeners.forEach((l) => l())
}
function subscribe(cb: () => void) {
  listeners.add(cb)
  return () => listeners.delete(cb)
}

export function showActionSheet(options: ActionSheetOptions): Promise<number> {
  state = { open: true, options }
  emit()
  return new Promise<number>((resolve) => {
    resolver = resolve
  })
}

function settle(idx: number) {
  resolver?.(idx)
  resolver = null
  state = { open: false, options: {} }
  emit()
}

export function ActionSheetHost() {
  const snap = useSyncExternalStore(subscribe, () => state)
  const { options } = snap
  return (
    <Dialog open={snap.open} onOpenChange={(o) => !o && settle(-1)}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          {options.title ? <DialogTitle>{options.title}</DialogTitle> : null}
          {options.message ? <DialogDescription>{options.message}</DialogDescription> : null}
        </DialogHeader>
        <DialogBody className="flex flex-col gap-2">
          {(options.actions || []).map((a, idx) => (
            <Button
              key={idx}
              variant={a.destructive ? 'destructive' : 'secondary'}
              className="w-full justify-center"
              onClick={() => settle(idx)}
            >
              {a.label}
            </Button>
          ))}
        </DialogBody>
        <DialogFooter>
          <DialogClose render={<Button variant="ghost" className="w-full justify-center" />}>
            取消
          </DialogClose>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
