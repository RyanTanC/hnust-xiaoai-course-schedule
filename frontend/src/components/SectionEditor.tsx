// Reusable per-section timetable editor (作息时间微调 逐节). Shared by the setup
// wizard (step 3) and the dashboard sync card. Ported from renderSectionEditor()
// and the section add/remove/validate logic in app.js.

import { Button } from '@appica/ui-react/button'
import { TimeField } from '@appica/ui-react/time-field'
import { TIME_RE, MIN_SECTIONS, MAX_SECTIONS, addMinutes } from '../lib/constants'
import type { SectionTime } from '../lib/types'

interface Props {
  sections: SectionTime[]
  onChange: (next: SectionTime[]) => void
  banner?: string | null
  /** scope label used only for stable keys/debugging */
  title?: string
}

export function SectionEditor({ sections, onChange, banner, title = '作息时间微调（逐节）' }: Props) {
  const setTime = (idx: number, field: 's' | 'e', val: string) => {
    const next = sections.map((row, i) => (i === idx ? { ...row, [field]: val } : row))
    onChange(next)
  }
  const addRow = () => {
    if (sections.length >= MAX_SECTIONS) return
    const last = sections[sections.length - 1]
    // A new row must start before it ends (the backend drops s >= e rows), so
    // default the end to one 45-minute slot after the start instead of s === e.
    const next: SectionTime = last
      ? { i: (last.i || sections.length) + 1, s: last.e, e: addMinutes(last.e, 45) }
      : { i: 1, s: '08:00', e: '08:45' }
    onChange([...sections, next])
  }
  const removeRow = () => {
    if (sections.length <= MIN_SECTIONS) return
    onChange(sections.slice(0, -1).map((r, i) => ({ ...r, i: i + 1 })))
  }
  const invalid = sections.some((r) => !TIME_RE.test(r.s) || !TIME_RE.test(r.e) || r.s >= r.e)

  return (
    <div className="rounded-xl border border-border p-3">
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="text-sm font-medium">{title}</span>
        <div className="flex gap-2">
          <Button size="sm" variant="outline" onClick={addRow} disabled={sections.length >= MAX_SECTIONS}>
            + 节次
          </Button>
          <Button size="sm" variant="outline" onClick={removeRow} disabled={sections.length <= MIN_SECTIONS}>
            − 节次
          </Button>
        </div>
      </div>
      <p className="mb-2 text-xs text-foreground-muted">
        按实际作息调整每节的开始/结束时间，同步时会一并写入云端课表设置。
      </p>
      {banner ? <div className="mb-2 rounded-lg bg-primary-soft px-3 py-2 text-xs">{banner}</div> : null}
      <div className="flex max-h-72 flex-col gap-2 overflow-y-auto">
        {sections.map((row, idx) => (
          <div key={row.i ?? idx} className="flex items-center gap-2">
            <span className="w-12 shrink-0 text-xs text-foreground-muted">第 {idx + 1} 节</span>
            <TimeField
              className="flex-1"
              format="HH:mm"
              value={row.s || null}
              onValueChange={(t) => setTime(idx, 's', t ?? '')}
            />
            <span className="text-foreground-muted">–</span>
            <TimeField
              className="flex-1"
              format="HH:mm"
              value={row.e || null}
              onValueChange={(t) => setTime(idx, 'e', t ?? '')}
            />
          </div>
        ))}
      </div>
      {invalid ? <p className="mt-2 text-xs text-red-500">存在无效节次（需为 HH:MM 且开始早于结束），请修正后再同步。</p> : null}
    </div>
  )
}
