// API payload types, aligned 1:1 with the Flask backend contracts in app.py.

export interface AppConfig {
  phone: string
  has_password: boolean
  has_token: boolean
  uuid: string
  debug_mode: boolean
  has_local_kb: boolean
  has_userinfo: boolean
  has_app_credentials: boolean
}

export interface TableSummary {
  id: string | number
  name: string
  /** 1 when this is the currently active cloud table. */
  current?: number
  [k: string]: unknown
}

export interface CourseItem {
  name: string
  teacher?: string
  position?: string
  day?: number | string
  sections?: string | number
  weeks?: string | number
  id?: string | number
  [k: string]: unknown
}

export interface TableDetail {
  id: string | number
  name: string
  courses: CourseItem[]
  setting?: Record<string, unknown> | null
  [k: string]: unknown
}

/** One editable timetable section row: index / start / end (HH:MM). */
export interface SectionTime {
  i: number
  s: string
  e: string
}

/** A season preset as returned by /api/schedule/rules (SCHEDULE_PRESETS entry). */
export interface Preset {
  label?: string
  rule?: Record<string, string>
  /** Raw [start, end] time tuples; convert via presetSections(). */
  sections?: [string, string][]
}

/** One selectable semester term from /api/semesters (suggest_semester_terms). */
export interface SemesterTerm {
  name: string
  label: string
  year?: number
  term?: number
}

export interface ScheduleMeta {
  slotDuration: number
  innerBreak: number
  longBreak: number
  groupSize: number
  defaultTotalSections: number
}

export interface ScheduleRulesResp {
  status: string
  rules: Record<string, Record<string, string>>
  presets: Record<string, Preset>
  meta: ScheduleMeta
  message?: string
}

export interface SyncParams {
  season: 'winter' | 'summer'
  startSemester: string
  presentWeek: number
  totalWeek: number
  m_num: number
  a_num: number
  n_num: number
  sections: SectionTime[]
  tableName: string
}

export interface SyncPreviewCourse {
  name: string
  teacher: string
  position: string
  day: number | string
  sections: string
  weeks: string
}

export interface SyncPreview {
  status: string
  available: boolean
  message?: string
  target?: { id: string | number; name: string; isCurrent: boolean; courseCount: number }
  source?: { type: string; courseCount: number }
  plan?: {
    toAdd: SyncPreviewCourse[]
    toDelete: SyncPreviewCourse[]
    unchanged: number
    addTruncated?: boolean
    deleteTruncated?: boolean
  }
  stats?: { add: number; delete: number; unchanged: number; operations: number }
  warnings?: string[]
}

export interface SyncProgressResult {
  status: string
  message: string
  stats?: { added: number; deleted: number; skipped: number }
  delete_failed?: string[]
  add_failed?: string[]
}

export interface SyncProgress {
  status?: 'running' | 'done' | 'error'
  task_id?: string
  phase?: string
  message?: string
  done?: number
  total?: number
  result?: SyncProgressResult | null
  started_at?: number
  finished_at?: number
}

export interface CaptureProgress {
  status?: 'running' | 'done' | 'error'
  count?: number
  message?: string
  stop_requested?: boolean
}

export interface SuggestResp {
  status: string
  source: 'local' | 'remote' | 'none'
  available: boolean
  message?: string
  sections: SectionTime[] | null
  reason?: string
  usedSections?: number[]
  maxUsed?: number
  groups?: Record<string, unknown>
  periods?: unknown
  groupSize?: number
  season?: string
}

export interface LoginResp {
  status: 'ok' | 'verification_needed' | 'error'
  message?: string
  token?: string
  token_preview?: string
  tables?: TableSummary[]
  verification_url?: string
  auth_mode?: string
  user_preview?: { userId: string; deviceId: string }
  code?: string
}

/** Generic envelope returned by most endpoints. */
export type ApiResp<T = Record<string, unknown>> = {
  status: string
  message?: string
  code?: string
} & T
