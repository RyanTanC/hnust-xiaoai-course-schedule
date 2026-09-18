// The sync engine UI: parameter form + section editor + (optional) rule panel +
// preview modal + async sync with progress polling. One source of truth shared by
// the setup wizard (showRules=false) and the dashboard (showRules=true).
//
// Faithful ports from app.js: buildSyncParams (m_num/a_num/n_num derived from the
// 5 / 9 section boundaries exactly as core.py segments them), doSync + poll of
// /api/sync/progress every 1s, previewSync modal, suggest/apply-preset/reset.

import { useCallback, useEffect, useRef, useState } from 'react'
import { Button } from '@appica/ui-react/button'
import { NumberField } from '@appica/ui-react/number-field'
import { DatePicker } from '@appica/ui-react/date-picker'
import { Badge } from '@appica/ui-react/badge'
import { Progress } from '@appica/ui-react/progress'
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from '@appica/ui-react/select'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@appica/ui-react/dialog'
import { SectionEditor } from './SectionEditor'
import { RulesPanel } from './RulesPanel'
import { api } from '../lib/api'
import { toast } from '../lib/toast'
import { MIN_SECTIONS, MAX_SECTIONS, TIME_RE, presetSections, addMinutes } from '../lib/constants'
import type {
  Preset,
  ScheduleRulesResp,
  SemesterTerm,
  SectionTime,
  SuggestResp,
  SyncParams,
  SyncPreview,
  SyncProgress,
  TableSummary,
} from '../lib/types'

const AFTERNOON_FIRST = 5
const EVENING_FIRST = 9

interface Props {
  showRules?: boolean
  /** currently selected cloud table id; falls back to current / first table */
  tid?: string | number | null
  tables: TableSummary[]
  onSynced?: () => void
  compact?: boolean
}

const SEASON_ITEMS = [
  { value: 'winter', label: '冬季' },
  { value: 'summer', label: '夏季' },
]

// Timezone-safe "yyyy-MM-dd" <-> local Date helpers for the DatePicker, whose
// value is a native Date while the backend contract (startSemester) is a string.
function isoToDate(s: string): Date | undefined {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s)
  if (!m) return undefined
  return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]))
}
function dateToISO(d: Date): string {
  const p = (x: number) => String(x).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

export function SyncSection({ showRules = false, tid, tables, onSynced, compact = false }: Props) {
  const [season, setSeason] = useState<'winter' | 'summer'>('winter')
  const [startSemester, setStartSemester] = useState('2026-03-02')
  const [presentWeek, setPresentWeek] = useState(1)
  const [totalWeek, setTotalWeek] = useState(20)
  const [tableName, setTableName] = useState('')
  const [terms, setTerms] = useState<SemesterTerm[]>([])
  const [presets, setPresets] = useState<Record<string, Preset>>({})
  const [sections, setSections] = useState<SectionTime[]>([])
  const [banner, setBanner] = useState<string | null>(null)

  const [preview, setPreview] = useState<SyncPreview | null>(null)
  const [previewOpen, setPreviewOpen] = useState(false)

  const [syncProg, setSyncProg] = useState<SyncProgress | null>(null)
  const [syncing, setSyncing] = useState(false)
  const pollRef = useRef<number | null>(null)

  // A section must be HH:MM with start strictly before end. The backend drops
  // invalid rows when building sections_data yet still trusts our m/a/n counts,
  // so an invalid row would desync the cloud timetable — block sync until fixed.
  const sectionsInvalid = sections.some((r) => !TIME_RE.test(r.s) || !TIME_RE.test(r.e) || r.s >= r.e)

  const stopPoll = useCallback(() => {
    if (pollRef.current !== null) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }, [])

  // Initial load: rules (presets/meta) + semesters.
  useEffect(() => {
    ;(async () => {
      const r = await api<ScheduleRulesResp>('/api/schedule/rules')
      if (r.status === 'ok') {
        setPresets(r.presets || {})
        const secs = presetSections(r.presets?.winter)
        if (secs.length) setSections(secs)
      }
      const sem = await api<{ status: string; current: string; terms: SemesterTerm[] }>('/api/semesters')
      if (sem.status === 'ok' && Array.isArray(sem.terms)) setTerms(sem.terms)
    })()
  }, [])

  // keep polling alive across parent re-renders if a sync is already running
  useEffect(() => () => stopPoll(), [stopPoll])

  const resizeSections = (count: number) => {
    const n = Math.min(MAX_SECTIONS, Math.max(MIN_SECTIONS, count || 0))
    setSections((prev) => {
      if (n === prev.length) return prev
      if (n < prev.length) return prev.slice(0, n).map((r, i) => ({ ...r, i: i + 1 }))
      const out = [...prev]
      for (let i = prev.length; i < n; i++) {
        const last = out[i - 1]
        out.push(last ? { i: i + 1, s: last.e, e: addMinutes(last.e, 45) } : { i: i + 1, s: '08:00', e: '08:45' })
      }
      return out
    })
  }

  const applySeasonPreset = useCallback(() => {
    const secs = presetSections(presets[season])
    if (secs.length) {
      setSections(secs)
      setBanner(null)
      toast(`已载入${season === 'winter' ? '冬季' : '夏季'}预设`, 'success')
    }
  }, [presets, season])

  // "重置" always restores the canonical winter baseline (discards manual edits),
  // so it differs from "载入季节预设" which reloads the currently-selected season.
  const resetSections = useCallback(() => {
    const secs = presetSections(presets['winter'])
    if (secs.length) {
      setSections(secs)
      setBanner(null)
      toast('已重置为默认作息', 'success')
    }
  }, [presets])

  const suggestSchedule = useCallback(async () => {
    const res = await api<SuggestResp>(`/api/schedule/suggest?season=${season}`)
    if (res.status === 'ok' && res.available && res.sections) {
      setSections(res.sections.map((x, i) => ({ i: x.i ?? i + 1, s: x.s, e: x.e })))
      setBanner(res.reason || '已按课表推荐节次')
      toast('已生成智能推荐节次', 'success')
    } else {
      setBanner(res.message || '暂无可用于推荐的课表数据')
      toast(res.message || '暂无可推荐数据，请先捕获课表', 'warning')
    }
  }, [season])

  const effectiveTid = tid || (tables.find((t) => t.current === 1) || tables[0])?.id || null

  const buildParams = (): SyncParams => ({
    season,
    startSemester,
    presentWeek,
    totalWeek,
    m_num: sections.filter((s) => (s.i ?? 0) < AFTERNOON_FIRST).length,
    a_num: sections.filter((s) => (s.i ?? 0) >= AFTERNOON_FIRST && (s.i ?? 0) < EVENING_FIRST).length,
    n_num: sections.filter((s) => (s.i ?? 0) >= EVENING_FIRST).length,
    sections,
    tableName: tableName.trim(),
  })

  const pollProgress = useCallback(async () => {
    const res = await api<{ status: string; progress: SyncProgress }>('/api/sync/progress')
    const prog = res.progress
    setSyncProg(prog)
    if (prog.status !== 'running') {
      stopPoll()
      setSyncing(false)
      if (prog.status === 'done') {
        toast(prog.result?.message || '同步完成', 'success')
        onSynced?.()
      } else if (prog.status === 'error') {
        toast(prog.result?.message || prog.message || '同步失败', 'error')
      }
    }
  }, [onSynced, stopPoll])

  const doSync = useCallback(async () => {
    if (!effectiveTid) {
      toast('请先选择要同步的课表', 'error')
      return
    }
    if (sectionsInvalid) {
      toast('存在无效节次（开始需早于结束），请先修正作息时间', 'error')
      return
    }
    const res = await api<{ status: string; message?: string }>('/api/sync', {
      method: 'POST',
      body: JSON.stringify({ tid: effectiveTid, params: buildParams() }),
    })
    if (res.status !== 'ok') {
      toast(res.message || '同步启动失败', 'error')
      return
    }
    setSyncing(true)
    setSyncProg({ status: 'running', phase: 'preparing', message: '正在准备同步...', done: 0, total: 0 })
    stopPoll()
    pollRef.current = window.setInterval(pollProgress, 1000)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [effectiveTid, season, startSemester, presentWeek, totalWeek, tableName, sections, sectionsInvalid, pollProgress, stopPoll])

  const openPreview = useCallback(async () => {
    const res = await api<SyncPreview>('/api/sync/preview', {
      method: 'POST',
      body: JSON.stringify({ tid: effectiveTid }),
    })
    setPreview(res)
    setPreviewOpen(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [effectiveTid])

  const progPct =
    syncProg && syncProg.total ? Math.round(((syncProg.done || 0) / syncProg.total) * 100) : null

  const tableNameItems = [
    { value: '', label: '（保持原名称）' },
    ...terms.map((t) => ({ value: t.name, label: t.label })),
  ]

  return (
    <div className={compact ? 'flex flex-col gap-3' : 'flex flex-col gap-4'}>
      {showRules ? (
        <RulesPanel
          onSectionsRegenerated={(secs) => {
            setSections(secs)
            setPresets((p) => ({ ...p }))
          }}
          presets={presets}
          season={season}
        />
      ) : null}

      {/* Parameter form */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <div className="flex flex-col gap-1 text-xs text-foreground-muted">
          <span>课表名称（重命名）</span>
          <Select
            value={tableName}
            onValueChange={(v) => setTableName(typeof v === 'string' ? v : '')}
            items={tableNameItems}
          >
            <SelectTrigger className="w-full">
              <SelectValue placeholder="（保持原名称）" />
            </SelectTrigger>
            <SelectContent>
              {tableNameItems.map((it) => (
                <SelectItem key={it.value} value={it.value}>
                  {it.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-col gap-1 text-xs text-foreground-muted">
          <span>学期起始日期</span>
          <DatePicker
            value={isoToDate(startSemester)}
            onValueChange={(d) => d && setStartSemester(dateToISO(d))}
            dateFormat="yyyy-MM-dd"
            className="w-full"
          />
        </div>
        <div className="flex flex-col gap-1 text-xs text-foreground-muted">
          <span>季节预设</span>
          <Select
            value={season}
            onValueChange={(v) => v && setSeason(v as 'winter' | 'summer')}
            items={SEASON_ITEMS}
          >
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {SEASON_ITEMS.map((it) => (
                <SelectItem key={it.value} value={it.value}>
                  {it.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-col gap-1 text-xs text-foreground-muted">
          <span>当前周次</span>
          <NumberField
            value={presentWeek}
            min={1}
            max={30}
            className="w-full"
            onValueChange={(v) => setPresentWeek(v ?? 1)}
          />
        </div>
        <div className="flex flex-col gap-1 text-xs text-foreground-muted">
          <span>总周数</span>
          <NumberField
            value={totalWeek}
            min={1}
            max={30}
            className="w-full"
            onValueChange={(v) => setTotalWeek(v ?? 20)}
          />
        </div>
        <div className="flex flex-col gap-1 text-xs text-foreground-muted">
          <span>节次数</span>
          <NumberField
            value={sections.length}
            min={MIN_SECTIONS}
            max={MAX_SECTIONS}
            className="w-full"
            onValueChange={(v) => resizeSections(v ?? sections.length)}
          />
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="primary" onClick={suggestSchedule}>
          ✨ 智能推荐
        </Button>
        <Button size="sm" variant="outline" onClick={applySeasonPreset}>
          载入季节预设
        </Button>
        <Button size="sm" variant="outline" onClick={resetSections}>
          重置
        </Button>
      </div>

      <SectionEditor sections={sections} onChange={setSections} banner={banner} />

      <div className="flex flex-wrap gap-3">
        <Button variant="outline" onClick={openPreview}>
          🔍 预览变更
        </Button>
        <Button onClick={doSync} disabled={syncing || sectionsInvalid}>
          同步到云端
        </Button>
      </div>

      {syncing || syncProg ? (
        <div className="flex flex-col gap-2 rounded-xl border border-border p-4">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium">同步进度</span>
            <Badge variant={syncProg?.status === 'error' ? 'error' : syncProg?.status === 'done' ? 'success' : 'warning'}>
              {syncProg?.status === 'done' ? '完成' : syncProg?.status === 'error' ? '失败' : '进行中'}
            </Badge>
          </div>
          <Progress value={progPct} />
          <p className="text-xs text-foreground-muted">{syncProg?.message || '正在准备同步...'}</p>
        </div>
      ) : null}

      {/* Preview modal */}
      <Dialog open={previewOpen} onOpenChange={setPreviewOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>同步预览</DialogTitle>
          </DialogHeader>
          <DialogBody className="max-h-[60vh] overflow-y-auto">
            <PreviewBody preview={preview} />
          </DialogBody>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPreviewOpen(false)}>
              关闭
            </Button>
            <Button
              onClick={() => {
                setPreviewOpen(false)
                doSync()
              }}
            >
              确认同步
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function PreviewBody({ preview }: { preview: SyncPreview | null }) {
  if (!preview) return <p className="text-sm text-foreground-muted">加载中…</p>
  if (preview.status !== 'ok') return <p className="text-sm text-red-500">{preview.message || '预览失败'}</p>
  if (!preview.available) return <p className="text-sm text-foreground-muted">{preview.message}</p>
  const p = preview.plan
  return (
    <div className="flex flex-col gap-3 text-sm">
      {preview.target ? (
        <p>
          目标课表：<b>{preview.target.name}</b>
          {preview.target.isCurrent ? <Badge className="ml-2" variant="success">当前</Badge> : null}
          <span className="ml-2 text-foreground-muted">云端 {preview.target.courseCount} 门</span>
        </p>
      ) : null}
      <div className="flex gap-3">
        <Badge variant="success">新增 {preview.stats?.add ?? 0}</Badge>
        <Badge variant="error">删除 {preview.stats?.delete ?? 0}</Badge>
        <Badge variant="outline">未变 {preview.stats?.unchanged ?? 0}</Badge>
      </div>
      {preview.warnings?.length ? (
        <div className="flex flex-col gap-1">
          {preview.warnings.map((w, i) => (
            <p key={i} className="text-orange-500">
              ⚠ {w}
            </p>
          ))}
        </div>
      ) : null}
      <CourseList title="将新增" courses={p?.toAdd || []} truncated={p?.addTruncated} />
      <CourseList title="将删除" courses={p?.toDelete || []} truncated={p?.deleteTruncated} />
    </div>
  )
}

function CourseList({
  title,
  courses,
  truncated,
}: {
  title: string
  courses: SyncPreview['plan'] extends infer T ? any : any
  truncated?: boolean
}) {
  if (!courses.length) return null
  return (
    <div>
      <p className="mb-1 text-xs font-medium text-foreground-muted">
        {title}（{courses.length}
        {truncated ? '，仅显示前 50 门' : ''}）
      </p>
      <div className="flex flex-col gap-1">
        {courses.map((c: any, i: number) => (
          <div key={i} className="rounded-lg border border-border px-2 py-1 text-xs">
            {c.name} · {c.teacher || '-'} · {c.position || '-'} · 周{c.day} 第{c.sections}节 第{c.weeks}周
          </div>
        ))}
      </div>
    </div>
  )
}
