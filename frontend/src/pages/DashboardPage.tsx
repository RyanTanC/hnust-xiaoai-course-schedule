// Main management page: cloud table list (select / create / delete / refresh),
// sync settings (rules + section editor + preview + sync), re-capture, per-table
// detail & weekly browser, and settings. Rebuilt from the dashboard half of app.js.

import { useCallback, useEffect, useState } from 'react'
import { Button } from '@appica/ui-react/button'
import { Card } from '@appica/ui-react/card'
import { Badge } from '@appica/ui-react/badge'
import { Input } from '@appica/ui-react/input'
import { Spinner } from '@appica/ui-react/spinner'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@appica/ui-react/dialog'
import {
  AlertDialog,
  AlertDialogBody,
  AlertDialogContent,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@appica/ui-react/alert-dialog'
import { api } from '../lib/api'
import { toast } from '../lib/toast'
import { showActionSheet } from '../lib/actionSheet'
import { useApp } from '../lib/store'
import { CACHE_DETAIL_PREFIX } from '../lib/constants'
import { SyncSection } from '../components/SyncSection'
import { CapturePanel } from '../components/CapturePanel'
import { WeeklyBrowser } from '../components/WeeklyBrowser'
import { SettingsDialog } from '../components/SettingsDialog'
import type { TableDetail } from '../lib/types'

export function DashboardPage() {
  const app = useApp()
  const [selectedId, setSelectedId] = useState<string | number | null>(null)
  const [detail, setDetail] = useState<TableDetail | null>(null)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [view, setView] = useState<'detail' | 'weekly'>('weekly')

  const [createOpen, setCreateOpen] = useState(false)
  const [newName, setNewName] = useState('')
  const [deleteTid, setDeleteTid] = useState<string | number | null>(null)
  const [captureOpen, setCaptureOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [debugMode, setDebugMode] = useState(!!app.config?.debug_mode)

  // Pull the cloud table list once when it's empty. Depend on the stable length
  // (not the whole `app` object, which is a new identity every render) so a user
  // with zero tables doesn't trigger an infinite refreshTables() re-render loop.
  useEffect(() => {
    if (app.tables.length === 0) app.refreshTables()
  }, [app.tables.length, app.refreshTables])

  useEffect(() => {
    setDebugMode(!!app.config?.debug_mode)
  }, [app.config])

  const loadDetail = useCallback(async (tid: string | number) => {
    setLoadingDetail(true)
    const res = await api<{ status: string; detail?: TableDetail; message?: string }>(`/api/table/${tid}`)
    if (res.status === 'ok' && res.detail) {
      setDetail(res.detail)
      try {
        localStorage.setItem(CACHE_DETAIL_PREFIX + tid, JSON.stringify(res.detail))
      } catch {
        /* ignore */
      }
    } else {
      toast(res.message || '获取详情失败', 'error')
    }
    setLoadingDetail(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const selectTable = (tid: string | number) => {
    setSelectedId(tid)
    loadDetail(tid)
  }

  const createTable = async () => {
    if (!newName.trim()) {
      toast('请输入课表名称', 'error')
      return
    }
    const res = await api<{ status: string; message?: string }>('/api/table/create', {
      method: 'POST',
      body: JSON.stringify({ name: newName.trim() }),
    })
    if (res.status === 'ok') {
      toast(res.message || '已创建', 'success')
      setCreateOpen(false)
      setNewName('')
      app.refreshTables()
    } else {
      toast(res.message || '创建失败', 'error')
    }
  }

  const requestDelete = async (tid: string | number, name: string) => {
    const idx = await showActionSheet({
      title: '删除课表',
      message: `确定要删除「${name}」吗？此操作不可撤销。`,
      actions: [{ label: '删除', destructive: true }],
    })
    if (idx === 0) setDeleteTid(tid)
  }

  const confirmDelete = async () => {
    if (deleteTid == null) return
    const res = await api<{ status: string; message?: string }>('/api/table/delete', {
      method: 'POST',
      body: JSON.stringify({ tid: deleteTid }),
    })
    if (res.status === 'ok') {
      toast('课表已删除', 'success')
      if (selectedId === deleteTid) {
        setSelectedId(null)
        setDetail(null)
      }
      app.refreshTables()
    } else {
      toast(res.message || '删除失败', 'error')
    }
    setDeleteTid(null)
  }

  const currentTable = app.tables.find((t) => t.current === 1) || app.tables[0]
  const totalWeeks = Number((detail?.setting as any)?.totalWeek) || 20
  // Single full-width column by default; split 50/50 into two columns only after a table is opened.
  const twoCol = selectedId != null

  return (
    <div className="mx-auto flex w-full max-w-7xl flex-col gap-6 px-4 py-6">
      {/* Left sidebar stacks list + sync settings; the browser fills the right main column */}
      <div className={twoCol ? 'grid grid-cols-1 items-start gap-6 lg:grid-cols-2' : 'flex flex-col gap-6'}>
        <div className="flex flex-col gap-6">
          {/* Table list */}
          <section>
            <Header
              title="课表列表"
              actions={
                <>
                  <Button size="sm" variant="outline" onClick={() => app.refreshTables()}>
                    ↻ 刷新
                  </Button>
                  <Button size="sm" onClick={() => setCreateOpen(true)}>
                    + 新建
                  </Button>
                </>
              }
            />
            <div className="mb-2 flex flex-wrap gap-2 text-xs text-foreground-muted">
              <Badge variant="outline">{app.tables.length} 个课表</Badge>
              {currentTable ? <Badge variant="info">当前：{currentTable.name}</Badge> : null}
              {app.config?.has_local_kb ? <Badge variant="success">已有本地课表</Badge> : <Badge variant="warning">未捕获本地课表</Badge>}
            </div>
            <div className="flex flex-col gap-2">
              {app.tables.map((t) => {
                const isSel = selectedId === t.id
                return (
                  <Card key={String(t.id)} frame className="p-3">
                    <div className="flex items-center justify-between gap-2">
                      <button className="flex min-w-0 flex-1 items-center gap-2 text-left" onClick={() => selectTable(t.id)}>
                        <span className="truncate text-sm font-medium">{t.name}</span>
                        {t.current === 1 ? <Badge variant="success">当前</Badge> : null}
                      </button>
                      <div className="flex shrink-0 gap-1">
                        <Button size="sm" variant={isSel ? 'primary' : 'outline'} onClick={() => selectTable(t.id)}>
                          查看
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => requestDelete(t.id, t.name)}>
                          删除
                        </Button>
                      </div>
                    </div>
                  </Card>
                )
              })}
              {app.tables.length === 0 ? (
                <p className="text-sm text-foreground-muted">还没有云端课表，点「+ 新建」创建一个。</p>
              ) : null}
            </div>
          </section>

          {/* Sync settings (moved into the sidebar so it no longer spans a full-width row) */}
          <section>
            <Header title="同步设置" />
            <Card frame className="p-4">
              <SyncSection
                showRules
                tid={selectedId ?? currentTable?.id ?? null}
                tables={app.tables}
                onSynced={() => {
                  app.refreshTables()
                  if (selectedId) loadDetail(selectedId)
                }}
              />
              <div className="mt-3 flex flex-wrap gap-3">
                <Button variant="outline" onClick={() => setCaptureOpen((v) => !v)}>
                  重新捕获课表
                </Button>
                <Button variant="outline" onClick={() => setSettingsOpen(true)}>
                  设置
                </Button>
              </div>
              {captureOpen ? (
                <div className="mt-3">
                  <CapturePanel
                    onFinished={() => {
                      toast('捕获完成，可预览同步', 'success')
                    }}
                  />
                </div>
              ) : null}
            </Card>
          </section>
        </div>

        {/* Right main: appears only after 「查看」, splitting the page 50/50 */}
        {twoCol ? (
          <section>
            <Header
              title="课表浏览与校对"
              actions={
                <>
                  <Button size="sm" variant={view === 'detail' ? 'primary' : 'outline'} onClick={() => setView('detail')}>
                    详情
                  </Button>
                  <Button size="sm" variant={view === 'weekly' ? 'primary' : 'outline'} onClick={() => setView('weekly')}>
                    逐周浏览
                  </Button>
                </>
              }
            />
            <Card frame className="p-4">
              {loadingDetail ? (
                <div className="flex items-center gap-2 text-sm text-foreground-muted">
                  <Spinner className="size-4" /> 加载中...
                </div>
              ) : detail ? (
                view === 'weekly' ? (
                  <WeeklyBrowser courses={detail.courses || []} totalWeeks={totalWeeks} />
                ) : (
                  <DetailList detail={detail} />
                )
              ) : (
                <p className="text-sm text-foreground-muted">暂无详情数据</p>
              )}
            </Card>
          </section>
        ) : null}
      </div>

      {/* Create dialog */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>新建课表</DialogTitle>
          </DialogHeader>
          <DialogBody>
            <Input placeholder="课表名称" value={newName} onChange={(e) => setNewName(e.target.value)} />
          </DialogBody>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setCreateOpen(false)}>
              取消
            </Button>
            <Button onClick={createTable}>创建</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete confirm */}
      <AlertDialog open={deleteTid != null} onOpenChange={(o) => !o && setDeleteTid(null)}>
        <AlertDialogContent className="max-w-sm">
          <AlertDialogHeader>
            <AlertDialogTitle>确认删除</AlertDialogTitle>
          </AlertDialogHeader>
          <AlertDialogBody>
            <p className="text-sm">删除后不可恢复，确定继续吗？</p>
          </AlertDialogBody>
          <AlertDialogFooter>
            <Button variant="ghost" onClick={() => setDeleteTid(null)}>
              取消
            </Button>
            <Button variant="destructive" onClick={confirmDelete}>
              删除
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <SettingsDialog
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        debugMode={debugMode}
        onDebugChange={setDebugMode}
      />
    </div>
  )
}

function Header({ title, actions }: { title: string; actions?: React.ReactNode }) {
  return (
    <div className="mb-3 flex items-center justify-between">
      <h2 className="text-lg font-semibold">{title}</h2>
      {actions ? <div className="flex gap-2">{actions}</div> : null}
    </div>
  )
}

function DetailList({ detail }: { detail: TableDetail }) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium">{detail.name}</span>
        <Badge variant="outline">{(detail.courses || []).length} 门</Badge>
      </div>
      <div className="flex flex-col gap-1">
        {(detail.courses || []).map((c, i) => (
          <div key={i} className="rounded-lg border border-border px-3 py-2 text-xs">
            <div className="font-medium">{c.name}</div>
            <div className="text-foreground-muted">
              周{String(c.day)} 第{String(c.sections)}节 · {c.position || '-'} · {c.teacher || '-'} · 第{String(c.weeks)}周
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
