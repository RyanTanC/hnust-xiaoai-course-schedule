// Weekly timetable browser + per-course verification (课表浏览与校对). Renders a
// 7-day × N-section grid for the selected week, marks courses active that week,
// and lets the user tick courses as verified. Rebuilt from the renderWeekGrid /
// prevWeek / nextWeek / toggleVerified logic in app.js.

import { useMemo, useState } from 'react'
import { Button } from '@appica/ui-react/button'
import { Checkbox } from '@appica/ui-react/checkbox'
import { DAY_NAMES, MIN_SECTIONS, MAX_SECTIONS } from '../lib/constants'
import type { CourseItem } from '../lib/types'

/** Parse a sections field like "3,4" / "3-4" into numeric section indices. */
function parseSections(raw: unknown): number[] {
  const s = String(raw ?? '')
  const out = new Set<number>()
  for (const part of s.split(/[,，]/)) {
    const t = part.trim()
    if (!t) continue
    const range = t.match(/^(\d+)\s*[-~]\s*(\d+)$/)
    if (range) {
      for (let i = Number(range[1]); i <= Number(range[2]); i++) out.add(i)
    } else if (/^\d+$/.test(t)) {
      out.add(Number(t))
    }
  }
  return [...out]
}

/** Parse a weeks field like "1-16" / "1,3,5" / "2-8(双)" into a number set. */
function parseWeeks(raw: unknown): Set<number> {
  const s = String(raw ?? '')
  const set = new Set<number>()
  const parity = /单/.test(s) ? 'odd' : /双/.test(s) ? 'even' : null
  for (const part of s.replace(/[()（）]/g, ' ').split(/[,，]/)) {
    const t = part.trim()
    if (!t) continue
    const range = t.match(/^(\d+)\s*[-~]\s*(\d+)/)
    if (range) {
      for (let i = Number(range[1]); i <= Number(range[2]); i++) set.add(i)
    } else if (/^\d+/.test(t)) {
      set.add(Number(t.match(/^\d+/)?.[0]))
    }
  }
  if (parity === 'odd') for (const w of [...set]) if (w % 2 === 0) set.delete(w)
  if (parity === 'even') for (const w of [...set]) if (w % 2 === 1) set.delete(w)
  return set
}

function courseKey(c: CourseItem): string {
  return `${c.name}|${c.day}|${c.sections}|${c.weeks}|${c.position}`
}

interface Props {
  courses: CourseItem[]
  totalWeeks: number
  maxSections?: number
}

export function WeeklyBrowser({ courses, totalWeeks, maxSections }: Props) {
  const [week, setWeek] = useState(1)
  const [verified, setVerified] = useState<Set<string>>(new Set())

  const sectionCount = useMemo(() => {
    let mx = maxSections || 0
    for (const c of courses) for (const s of parseSections(c.sections)) mx = Math.max(mx, s)
    return Math.min(MAX_SECTIONS, Math.max(MIN_SECTIONS, mx || 12))
  }, [courses, maxSections])

  // cell map: day(1-7) -> section -> courses[]
  const grid = useMemo(() => {
    const g: Record<string, CourseItem[]> = {}
    for (const c of courses) {
      const secs = parseSections(c.sections)
      const weeks = parseWeeks(c.weeks)
      if (weeks.size && !weeks.has(week)) continue
      const day = Number(c.day)
      if (!day || day < 1 || day > 7) continue
      for (const sec of secs) {
        const k = `${day}-${sec}`
        ;(g[k] ||= []).push(c)
      }
    }
    return g
  }, [courses, week])

  const weekCourses = useMemo(() => {
    const seen = new Set<string>()
    const out: CourseItem[] = []
    for (const c of courses) {
      const weeks = parseWeeks(c.weeks)
      if (weeks.size && !weeks.has(week)) continue
      const k = courseKey(c)
      if (seen.has(k)) continue
      seen.add(k)
      out.push(c)
    }
    return out
  }, [courses, week])

  const toggle = (k: string) =>
    setVerified((prev) => {
      const next = new Set(prev)
      next.has(k) ? next.delete(k) : next.add(k)
      return next
    })

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <Button size="sm" variant="outline" disabled={week <= 1} onClick={() => setWeek((w) => Math.max(1, w - 1))}>
          ◀ 上一周
        </Button>
        <span className="min-w-16 text-center text-sm font-medium">第 {week} 周</span>
        <Button
          size="sm"
          variant="outline"
          disabled={week >= totalWeeks}
          onClick={() => setWeek((w) => Math.min(totalWeeks, w + 1))}
        >
          下一周 ▶
        </Button>
        <span className="text-xs text-foreground-muted">共 {totalWeeks} 周</span>
      </div>

      <div className="overflow-x-auto rounded-xl border border-border">
        <table className="w-full border-collapse text-xs">
          <thead>
            <tr>
              <th className="border-b border-border p-2 text-foreground-muted">节次</th>
              {DAY_NAMES.map((d) => (
                <th key={d} className="border-b border-border p-2 font-medium">
                  {d}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Array.from({ length: sectionCount }, (_, i) => i + 1).map((sec) => (
              <tr key={sec}>
                <td className="border-r border-border p-2 text-center align-top text-foreground-muted">{sec}</td>
                {DAY_NAMES.map((_, di) => {
                  const day = di + 1
                  const cell = grid[`${day}-${sec}`] || []
                  return (
                    <td key={day} className="h-16 min-w-20 border-r border-border p-1 align-top last:border-r-0">
                      {cell.map((c, ci) => (
                        <div key={ci} className="mb-1 rounded-md bg-primary-soft p-1 text-[11px] leading-tight">
                          <div className="font-medium">{c.name}</div>
                          <div className="text-foreground-muted">{c.position || ''}</div>
                          <div className="text-foreground-muted">{c.teacher || ''}</div>
                        </div>
                      ))}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="rounded-xl border border-border p-3">
        <p className="mb-2 text-xs font-medium text-foreground-muted">
          本周课程校对（{weekCourses.length} 门 · 已校对 {verified.size}）
        </p>
        <div className="flex flex-col gap-1">
          {weekCourses.map((c) => {
            const k = courseKey(c)
            return (
              <label key={k} className="flex items-center gap-2 text-xs">
                <Checkbox checked={verified.has(k)} onCheckedChange={() => toggle(k)} />
                <span>
                  {c.name} · 周{c.day} 第{c.sections}节 · {c.position || '-'} · {c.teacher || '-'}
                </span>
              </label>
            )
          })}
          {weekCourses.length === 0 ? <span className="text-xs text-foreground-muted">本周无课程</span> : null}
        </div>
      </div>
    </div>
  )
}
