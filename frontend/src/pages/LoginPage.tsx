// Setup wizard = login page. Step 1 offers the three auth modes (password /
// QR / userinfo import) plus the browser security-verification branch; step 2 is
// capture; step 3 is the compact sync section. Ported from doLogin()/verify/qr/
// userinfo handlers and the setup-step navigation in app.js.

import { useCallback, useEffect, useRef, useState } from 'react'
import { Button } from '@appica/ui-react/button'
import { Input } from '@appica/ui-react/input'
import { Card } from '@appica/ui-react/card'
import { Badge } from '@appica/ui-react/badge'
import { Spinner } from '@appica/ui-react/spinner'
import { Avatar, AvatarFallback } from '@appica/ui-react/avatar'
import { api } from '../lib/api'
import { toast } from '../lib/toast'
import { addLog } from '../lib/log'
import { useApp } from '../lib/store'
import { CapturePanel } from '../components/CapturePanel'
import { SyncSection } from '../components/SyncSection'
import type { LoginResp } from '../lib/types'

type Mode = 'password' | 'qr' | 'userinfo' | null

export function LoginPage() {
  const app = useApp()
  const [step, setStep] = useState(1)
  const [mode, setMode] = useState<Mode>(null)
  const [phone, setPhone] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [hint, setHint] = useState('')
  const [error, setError] = useState('')

  // verification branch state
  const [verifyUrl, setVerifyUrl] = useState('')
  const [verifyMsg, setVerifyMsg] = useState('')
  const verifyPoll = useRef<number | null>(null)

  // qr branch state
  const [qrMsg, setQrMsg] = useState('')
  const qrPoll = useRef<number | null>(null)

  useEffect(() => {
    if (app.config?.phone) setPhone(app.config.phone)
  }, [app.config])

  const stopTimers = useCallback(() => {
    if (verifyPoll.current) clearInterval(verifyPoll.current)
    if (qrPoll.current) clearInterval(qrPoll.current)
    verifyPoll.current = null
    qrPoll.current = null
  }, [])
  useEffect(() => () => stopTimers(), [stopTimers])

  const afterLogin = useCallback(
    (res: LoginResp, fallbackMsg: string) => {
      // Persist auth but stay in the wizard; it drives capture (step 2) -> sync (step 3).
      app.completeLogin(res.token, res.tables, res.message || fallbackMsg, false)
    },
    [app],
  )

  // ── password ──
  const doLogin = useCallback(async () => {
    setError('')
    if (!phone.trim() || !password) {
      setError('请填写手机号和密码')
      return
    }
    setBusy(true)
    setHint('登录中...')
    const res = await api<LoginResp>('/api/login', {
      method: 'POST',
      body: JSON.stringify({ phone: phone.trim(), password, use_cache: true }),
    })
    setBusy(false)
    setHint('')
    if (res.status === 'ok') {
      afterLogin(res, '登录成功')
      setStep(2)
    } else if (res.status === 'verification_needed') {
      setVerifyUrl(res.verification_url || '')
      setVerifyMsg(res.message || '需要安全验证')
      setError('')
    } else {
      setError(res.message || '登录失败')
    }
  }, [phone, password, afterLogin])

  const startVerifyBrowser = useCallback(async () => {
    setError('')
    stopTimers() // guard against a double-click spawning two concurrent poll loops
    const res = await api<{ status: string; message?: string }>('/api/login/verify-browser', { method: 'POST' })
    if (res.status !== 'ok') {
      setError(res.message || '无法启动验证')
      return
    }
    setVerifyMsg('浏览器已打开，请在其中完成小米安全验证...')
    verifyPoll.current = window.setInterval(async () => {
      const p = await api<{ status?: string; message?: string }>('/api/login/verify-status')
      if (p.status === 'running') {
        setVerifyMsg(p.message || '验证中...')
        return
      }
      if (verifyPoll.current) clearInterval(verifyPoll.current)
      verifyPoll.current = null
      if (p.status === 'done') {
        const fin = await api<LoginResp>('/api/login/verify-finalize', { method: 'POST' })
        if (fin.status === 'ok') {
          afterLogin(fin, '登录成功')
          setStep(2)
        } else {
          setError(fin.message || '验证收尾失败')
        }
      } else {
        setError(p.message || '验证失败')
        setVerifyMsg('')
      }
    }, 1500)
  }, [afterLogin, stopTimers])

  // ── qr ──
  const startQr = useCallback(async () => {
    setError('')
    stopTimers() // guard against a double-click spawning two concurrent poll loops
    const res = await api<{ status: string; message?: string }>('/api/login/qr-start', { method: 'POST' })
    if (res.status !== 'ok') {
      setError(res.message || '无法启动扫码')
      return
    }
    setQrMsg('二维码已生成，请在手机上确认...')
    qrPoll.current = window.setInterval(async () => {
      const p = await api<{ status?: string; message?: string }>('/api/login/qr-status')
      if (p.status === 'running') {
        setQrMsg(p.message || '等待扫码...')
        return
      }
      if (qrPoll.current) clearInterval(qrPoll.current)
      qrPoll.current = null
      if (p.status === 'done') {
        const fin = await api<LoginResp>('/api/login/qr-finalize', { method: 'POST' })
        if (fin.status === 'ok') {
          afterLogin(fin, '扫码登录成功')
          setStep(2)
        } else {
          setError(fin.message || '扫码收尾失败')
        }
      } else {
        setError(p.message || '扫码登录失败')
        setQrMsg('')
      }
    }, 1500)
  }, [afterLogin, stopTimers])

  // ── userinfo import ──
  const doUserinfo = useCallback(
    async (force: boolean) => {
      setError('')
      setBusy(true)
      const res = await api<LoginResp>('/api/login/userinfo', {
        method: 'POST',
        body: JSON.stringify({ force_reload: force }),
      })
      setBusy(false)
      if (res.status === 'ok') {
        addLog('info', `userinfo 登录成功 userId=${res.user_preview?.userId ?? '?'}`)
        afterLogin(res, 'userinfo.txt 导入成功')
        setStep(2)
      } else {
        const codeHint: Record<string, string> = {
          file_not_found: '未找到 userinfo.txt，请先写入抓包凭证',
          invalid_format: 'userinfo.txt 格式有误',
          token_invalid: 'Token 验证失败，可能已过期',
          token_expired: 'Token 已失效，请重新抓包更新',
        }
        setError(res.message || codeHint[res.code || ''] || '导入失败')
      }
    },
    [afterLogin],
  )

  const renderStepIndicator = () => (
    <div className="mb-8 flex items-center justify-center gap-1">
      {['登录账号', '捕获课表', '同步云端'].map((label, i) => {
        const n = i + 1
        const active = step === n
        const done = step > n
        return (
          <div key={label} className="flex items-center gap-1">
            <div
              className={
                'flex items-center gap-2 text-xs transition-colors ' +
                (active ? 'text-primary' : done ? 'text-success' : 'text-foreground-muted')
              }
            >
              <span
                className={
                  'flex size-7 items-center justify-center rounded-full border text-xs font-semibold transition-all ' +
                  (active
                    ? 'border-primary bg-primary text-white ring-4 ring-primary/15'
                    : done
                      ? 'border-success bg-success text-white'
                      : 'border-border bg-background')
                }
              >
                {done ? '✓' : n}
              </span>
              <span className={active ? 'font-medium' : undefined}>{label}</span>
            </div>
            {n < 3 ? (
              <span
                className={
                  'mx-1 h-px w-6 transition-colors sm:w-10 ' + (step > n ? 'bg-success/60' : 'bg-border')
                }
              />
            ) : null}
          </div>
        )
      })}
    </div>
  )

  const renderStep1 = () => (
    <Card frame className="mx-auto w-full max-w-md p-5">
      {!mode ? (
        <>
          <div className="mb-3 text-sm font-medium">选择登录方式</div>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            <MethodBtn icon="🔑" label="密码登录" desc="小米账号+密码" onClick={() => setMode('password')} />
            <MethodBtn icon="📱" label="扫码登录" desc="手机扫码快捷" onClick={() => setMode('qr')} />
            <MethodBtn icon="📋" label="Token 导入" desc="抓包凭证导入" onClick={() => setMode('userinfo')} />
          </div>
        </>
      ) : mode === 'password' ? (
        <div className="flex flex-col gap-3">
          <ModeHeader title="密码登录" onBack={() => setMode(null)} />
          <Input placeholder="手机号" value={phone} onChange={(e) => setPhone(e.target.value)} />
          <Input
            type="password"
            placeholder="密码"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          {verifyUrl ? (
            <div className="flex flex-col gap-2 rounded-lg border border-orange-300 bg-orange-500/5 p-3">
              <p className="text-xs text-foreground">{verifyMsg}</p>
              <div className="flex gap-2">
                <Button size="sm" onClick={startVerifyBrowser}>
                  启动浏览器验证
                </Button>
                <a
                  href={verifyUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center text-xs text-primary underline"
                >
                  打开验证链接
                </a>
              </div>
            </div>
          ) : null}
          <Button className="justify-center" onClick={doLogin} disabled={busy}>
            {busy ? <Spinner className="mr-2 size-4" /> : null}
            {busy ? '登录中...' : '登录'}
          </Button>
        </div>
      ) : mode === 'qr' ? (
        <div className="flex flex-col gap-3">
          <ModeHeader title="扫码登录" onBack={() => setMode(null)} />
          <p className="text-xs text-foreground-muted">{qrMsg || '点击下方按钮启动扫码流程，在弹出的页面用手机小米 App 扫码。'}</p>
          <Button className="justify-center" onClick={startQr}>
            启动扫码
          </Button>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <ModeHeader title="Token 导入" onBack={() => setMode(null)} />
          <p className="text-xs text-foreground-muted">
            读取服务器上的 data/userinfo.txt（抓包凭证）直接登录。适合已抓到有效 token 的老用户。
          </p>
          <div className="flex gap-2">
            <Button className="flex-1 justify-center" onClick={() => doUserinfo(false)} disabled={busy}>
              {busy ? <Spinner className="mr-2 size-4" /> : null}导入登录
            </Button>
            <Button variant="outline" onClick={() => doUserinfo(true)} disabled={busy}>
              强制刷新
            </Button>
          </div>
        </div>
      )}
      {error ? <p className="mt-3 text-sm text-red-500">{error}</p> : null}
      {hint ? <p className="mt-3 text-center text-xs text-foreground-muted">{hint}</p> : null}
    </Card>
  )

  const renderStep2 = () => (
    <Card frame className="mx-auto flex w-full max-w-md flex-col gap-3 p-5">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">捕获教务课表</span>
        <Badge variant="info">必须</Badge>
      </div>
      <p className="text-xs text-foreground-muted">
        系统将打开浏览器访问教务系统，请登录后切换到课表页面，然后关闭浏览器。
      </p>
      <CapturePanel onFinished={() => toast('课表捕获完成，可进入下一步', 'success')} />
      <div className="flex gap-2">
        <Button variant="outline" className="flex-1 justify-center" onClick={() => setStep(3)}>
          跳过此步
        </Button>
        <Button className="flex-1 justify-center" onClick={() => setStep(3)}>
          下一步
        </Button>
      </div>
    </Card>
  )

  const renderStep3 = () => (
    <Card frame className="mx-auto flex w-full max-w-lg flex-col gap-3 p-5">
      <div className="text-sm font-medium">同步到云端</div>
      <p className="text-xs text-foreground-muted">
        将本地课表同步到小爱课程表云端，完成后即可在手机 App 中查看。也可稍后在主界面操作。
      </p>
      <SyncSection
        compact
        tables={app.tables}
        onSynced={() => app.refreshTables()}
      />
      <Button variant="ghost" className="justify-center" onClick={() => app.setPage('dashboard')}>
        稍后再说，进入主界面
      </Button>
    </Card>
  )

  return (
    <div className="mx-auto w-full max-w-2xl px-4 py-8">
      <div className="mb-6 text-center">
        <h1 className="text-2xl font-semibold">小爱课程表</h1>
        <p className="text-sm text-foreground-muted">HNUST 教务助手</p>
      </div>
      {renderStepIndicator()}
      {step === 1 ? renderStep1() : step === 2 ? renderStep2() : renderStep3()}
      {step === 2 || step === 3 ? (
        <div className="mx-auto mt-4 flex max-w-md justify-end gap-2">
          {step === 3 ? (
            <Button variant="outline" size="sm" onClick={() => setStep(2)}>
              上一步
            </Button>
          ) : (
            <Button variant="outline" size="sm" onClick={() => setStep(1)}>
              上一步
            </Button>
          )}
        </div>
      ) : null}
      {app.config && !app.config.has_app_credentials ? (
        <p className="mx-auto mt-6 max-w-md text-center text-xs text-orange-500">
          未检测到小米应用凭证（.env 中的 XIAOMI_CLIENT_ID / XIAOMI_CLIENT_SECRET），密码与扫码登录可能不可用。
        </p>
      ) : null}
    </div>
  )
}

function MethodBtn({ icon, label, desc, onClick }: { icon: string; label: string; desc: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="group flex flex-col items-center gap-2 rounded-xl border border-border bg-background p-4 text-center transition-all hover:-translate-y-0.5 hover:border-primary hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
    >
      <Avatar size="lg" shape="rounded" className="ring-1 ring-border">
        <AvatarFallback className="bg-primary-soft/60 text-xl transition-colors group-hover:bg-primary-soft">
          {icon}
        </AvatarFallback>
      </Avatar>
      <span className="text-sm font-medium">{label}</span>
      <span className="text-xs text-foreground-muted">{desc}</span>
    </button>
  )
}

function ModeHeader({ title, onBack }: { title: string; onBack: () => void }) {
  return (
    <div className="flex items-center gap-2">
      <Button size="sm" variant="ghost" onClick={onBack}>
        ← 返回
      </Button>
      <span className="text-sm font-medium">{title}</span>
    </div>
  )
}
