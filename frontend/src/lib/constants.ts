// Storage keys and hard-coded UI constants ported from the original static/js/app.js.

import type { Preset, SectionTime } from './types'

/**
 * Convert a season preset (whose `sections` are raw [start, end] tuples from the
 * backend SCHEDULE_PRESETS) into editable {i,s,e} rows.
 */
export function presetSections(preset?: Preset | null): SectionTime[] {
  return (preset?.sections || []).map((pair, i) => ({ i: i + 1, s: pair[0], e: pair[1] }))
}

/** Add `mins` to an HH:MM string, wrapping around the day. Used to seed valid
 * new rows (a section must satisfy start < end or the backend drops it). */
export function addMinutes(hhmm: string, mins: number): string {
  if (!/^\d{2}:\d{2}$/.test(hhmm)) return hhmm
  const [h, m] = hhmm.split(':').map(Number)
  let t = h * 60 + m + mins
  if (t >= 1440) t -= 1440
  if (t < 0) t += 1440
  const hh = String(Math.floor(t / 60)).padStart(2, '0')
  const mm = String(t % 60).padStart(2, '0')
  return `${hh}:${mm}`
}

export const AUTH_TOKEN_KEY = 'aischedule_token'
export const PHONE_KEY = 'aischedule_phone'
export const CACHE_KEY = 'aischedule_tables_cache'
export const CACHE_DETAIL_PREFIX = 'aischedule_detail_'

/** HH:MM (24h) validator, identical regex to the original app. */
export const TIME_RE = /^([01]\d|2[0-3]):([0-5]\d)$/

export const MIN_SECTIONS = 2
export const MAX_SECTIONS = 16

export const DAY_NAMES = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

/** Capture progress 4-step map (index into a horizontal stepper). */
export const CAPTURE_STEPS_MAP: { key: string; label: string }[] = [
  { key: 'launching', label: '启动浏览器' },
  { key: 'loading', label: '打开教务系统' },
  { key: 'capturing', label: '捕获课表数据' },
  { key: 'finishing', label: '完成收尾' },
]

export const MAX_LOG_ENTRIES = 200
