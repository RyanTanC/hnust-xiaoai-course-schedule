// School-wide schedule rule editor (作息规则). Dashboard-only. Saves overrides via
// POST /api/schedule/rules and hands the regenerated preset sections back to the
// parent so the section editor reflects the new rule. Ported from
// saveRuleSettings()/resetRuleSettings().

import { useEffect, useState } from 'react'
import { Button } from '@appica/ui-react/button'
import { NumberField } from '@appica/ui-react/number-field'
import { TimeField } from '@appica/ui-react/time-field'
import { api } from '../lib/api'
import { toast } from '../lib/toast'
import { presetSections } from '../lib/constants'
import type { Preset, ScheduleRulesResp, SectionTime } from '../lib/types'

interface RuleForm {
  slotDuration: number
  innerBreak: number
  longBreak: number
  groupSize: number
  winterAfternoonStart: string
  winterEveningStart: string
  summerAfternoonStart: string
  summerEveningStart: string
}

const DEFAULTS: RuleForm = {
  slotDuration: 45,
  innerBreak: 10,
  longBreak: 20,
  groupSize: 2,
  winterAfternoonStart: '14:30',
  winterEveningStart: '19:30',
  summerAfternoonStart: '14:00',
  summerEveningStart: '19:00',
}

interface Props {
  season: 'winter' | 'summer'
  presets: Record<string, Preset>
  onSectionsRegenerated: (secs: SectionTime[]) => void
}

export function RulesPanel({ season, presets, onSectionsRegenerated }: Props) {
  const [form, setForm] = useState<RuleForm>(DEFAULTS)

  useEffect(() => {
    ;(async () => {
      const res = await api<ScheduleRulesResp>('/api/schedule/rules')
      if (res.status === 'ok' && res.rules) {
        const w = res.rules.winter || {}
        const s = res.rules.summer || {}
        setForm({
          slotDuration: res.meta?.slotDuration ?? 45,
          innerBreak: res.meta?.innerBreak ?? 10,
          longBreak: res.meta?.longBreak ?? 20,
          groupSize: res.meta?.groupSize ?? 2,
          winterAfternoonStart: w.afternoonStart || '14:30',
          winterEveningStart: w.eveningStart || '19:30',
          summerAfternoonStart: s.afternoonStart || '14:00',
          summerEveningStart: s.eveningStart || '19:00',
        })
      }
    })()
  }, [])

  const set = (k: keyof RuleForm, v: string | number) => setForm((f) => ({ ...f, [k]: v }))

  const save = async () => {
    const res = await api<ScheduleRulesResp>('/api/schedule/rules', {
      method: 'POST',
      body: JSON.stringify(form),
    })
    if (res.status === 'ok') {
      toast('作息规则已保存', 'success')
      const secs = presetSections(res.presets?.[season])
      if (secs.length) onSectionsRegenerated(secs)
    } else {
      toast(res.message || '保存失败', 'error')
    }
  }

  const reset = async () => {
    const res = await api<ScheduleRulesResp>('/api/schedule/rules', {
      method: 'POST',
      body: JSON.stringify({ ...DEFAULTS, reset: true }),
    })
    if (res.status === 'ok') {
      setForm(DEFAULTS)
      toast('已恢复本校默认规则', 'success')
      const secs = presetSections(res.presets?.[season])
      if (secs.length) onSectionsRegenerated(secs)
    }
  }

  return (
    <div className="flex flex-col gap-2 rounded-xl border border-border p-3">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">作息规则（本校）</span>
        <div className="flex gap-2">
          <Button size="sm" variant="outline" onClick={save}>
            保存规则
          </Button>
          <Button size="sm" variant="outline" onClick={reset}>
            恢复本校默认
          </Button>
        </div>
      </div>
      <p className="text-xs text-foreground-muted">
        改这里的参数后点「保存规则」，会按规则重新生成节次时间；再点「✨ 智能推荐」按你的课表收敛节数。
      </p>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <FieldNum label="单节时长" min={15} max={120} value={form.slotDuration} onChange={(v) => set('slotDuration', v)} />
        <FieldNum label="小课间" min={0} max={60} value={form.innerBreak} onChange={(v) => set('innerBreak', v)} />
        <FieldNum label="大课间" min={0} max={120} value={form.longBreak} onChange={(v) => set('longBreak', v)} />
        <FieldNum label="每组连上" min={1} max={6} value={form.groupSize} onChange={(v) => set('groupSize', v)} />
        <FieldTime label="冬季下午起" value={form.winterAfternoonStart} onChange={(v) => set('winterAfternoonStart', v)} />
        <FieldTime label="冬季晚修起" value={form.winterEveningStart} onChange={(v) => set('winterEveningStart', v)} />
        <FieldTime label="夏季下午起" value={form.summerAfternoonStart} onChange={(v) => set('summerAfternoonStart', v)} />
        <FieldTime label="夏季晚修起" value={form.summerEveningStart} onChange={(v) => set('summerEveningStart', v)} />
      </div>
    </div>
  )
}

function FieldNum({
  label,
  value,
  min,
  max,
  onChange,
}: {
  label: string
  value: number
  min: number
  max: number
  onChange: (v: number) => void
}) {
  return (
    <div className="flex flex-col gap-1 text-xs text-foreground-muted">
      <span>{label}（分钟）</span>
      <NumberField
        value={value}
        min={min}
        max={max}
        className="w-full"
        onValueChange={(v) => onChange(v ?? min)}
      />
    </div>
  )
}

function FieldTime({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <div className="flex flex-col gap-1 text-xs text-foreground-muted">
      <span>{label}</span>
      <TimeField format="HH:mm" value={value || null} onValueChange={(t) => onChange(t ?? '')} />
    </div>
  )
}
