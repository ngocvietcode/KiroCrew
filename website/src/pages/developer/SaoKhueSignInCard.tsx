import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  Building2,
  Check,
  CheckCircle2,
  Copy,
  ExternalLink,
  KeyRound,
  Link2,
  Loader2,
  LogOut,
  RefreshCw,
} from 'lucide-react'
import {
  saokhueLoginApi,
  type SaoKhueDeviceSession,
  type SaoKhueLoginStatus,
} from '../../api/saokhueLogin'
import { clearCachedModels } from '../../providers/adapters/acp'
import ErrorNotice from '../../components/ErrorNotice'
import { SettingsField, SettingsInput, SettingsSelect } from '../../components/settings'
import { Btn, Card, CardTitle, Input } from '../../components/ui'
import { fmtDateTime, fmtRelative } from '../../i18n/format'
import { i18nT } from '../../i18n/t'
import { copyToClipboard } from '../../utils/clipboard'

export const SAOKHUE_SIGN_IN_QUERY_KEY = ['saokhue-login'] as const
export const SAOKHUE_SIGN_IN_ANCHOR = 'saokhue-sign-in'

type LoginTab = 'sso' | 'device' | 'token' | 'password'

function CopyCodeField({ value }: { value: string }) {
  const [copied, setCopied] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current)
    },
    [],
  )
  const onCopy = async () => {
    if (!(await copyToClipboard(value))) return
    setCopied(true)
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => setCopied(false), 1500)
  }
  const label = copied
    ? i18nT('components.kasLogin.copied')
    : i18nT('components.kasLogin.copy_link')
  return (
    <button
      type="button"
      onClick={onCopy}
      aria-label={label}
      title={label}
      className="group/copy mt-1.5 flex w-full cursor-pointer items-center justify-between gap-2 rounded-lg border border-border bg-bg-elevated px-3 py-2 text-left hover:bg-bg-hover focus-ring"
    >
      <code className="min-w-0 overflow-x-auto font-mono text-[13px] text-text-strong">
        {value}
      </code>
      {copied ? (
        <Check className="lucide-inline shrink-0 text-ok" />
      ) : (
        <Copy className="lucide-inline shrink-0 text-muted transition-opacity group-hover/copy:opacity-100" />
      )}
    </button>
  )
}

function SignedInView({
  status,
  onSwitchAccount,
  onSignedOut,
}: {
  status: SaoKhueLoginStatus
  onSwitchAccount: () => void
  onSignedOut: () => void
}) {
  const queryClient = useQueryClient()
  const [modelDraft, setModelDraft] = useState(status.default_model || '')

  useEffect(() => {
    setModelDraft(status.default_model || '')
  }, [status.default_model])

  const refreshModels = useMutation({
    mutationFn: () => saokhueLoginApi.status(true),
    onSuccess: (fresh) => {
      queryClient.setQueryData(SAOKHUE_SIGN_IN_QUERY_KEY, fresh)
      clearCachedModels()
      void queryClient.invalidateQueries({ queryKey: ['available-models'] })
    },
  })

  const saveModel = useMutation({
    mutationFn: (nextModel: string) => saokhueLoginApi.setModel(nextModel),
    onSuccess: (fresh) => {
      queryClient.setQueryData(SAOKHUE_SIGN_IN_QUERY_KEY, fresh)
      clearCachedModels()
      void queryClient.invalidateQueries({ queryKey: ['available-models'] })
    },
  })

  const logout = useMutation({
    mutationFn: () => saokhueLoginApi.logout(),
    onSuccess: (fresh) => {
      onSignedOut()
      queryClient.setQueryData(SAOKHUE_SIGN_IN_QUERY_KEY, fresh)
      clearCachedModels()
      void queryClient.resetQueries({ queryKey: ['available-models'] })
    },
  })

  const lapsed = status.expired && !status.has_refresh_token
  const providerDisplay = [
    status.user_id,
    status.email ? `<${status.email}>` : '',
    status.auth_method ? `(${status.auth_method})` : '',
  ]
    .filter(Boolean)
    .join(' ')

  const expiresIso =
    typeof status.expires_at === 'number' && status.expires_at > 0
      ? new Date(status.expires_at * 1000).toISOString()
      : null

  const expiryLine = status.expired
    ? status.has_refresh_token
      ? i18nT('pages.developer.kiroSignInCard.expired_renews_on_next_use')
      : i18nT('pages.developer.kiroSignInCard.expired_no_refresh')
    : expiresIso
      ? i18nT('pages.developer.kiroSignInCard.access_token_expires', {
          when: fmtRelative(expiresIso),
          at: fmtDateTime(expiresIso),
        })
      : ''

  return (
    <div data-testid="saokhue-sign-in-summary" data-lapsed={lapsed ? 'true' : 'false'}>
      <div className="flex flex-wrap items-start gap-3">
        <div
          className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${
            lapsed ? 'bg-warn/15 text-warn-text' : 'bg-ok/15 text-ok-text'
          }`}
          aria-hidden="true"
        >
          {lapsed ? (
            <AlertTriangle className="lucide-inline" />
          ) : (
            <CheckCircle2 className="lucide-inline" />
          )}
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-text-strong" data-testid="saokhue-sign-in-state">
            {lapsed
              ? i18nT('pages.developer.kiroSignInCard.sign_in_expired')
              : i18nT('pages.developer.kiroSignInCard.signed_in_with', {
                  provider: providerDisplay,
                })}
          </p>
          <p className="mt-0.5 font-mono text-[12px] text-muted">{status.base_url}</p>
          {expiryLine ? (
            <p className="mt-1 text-[12px] text-muted" data-testid="saokhue-sign-in-expiry">
              {expiryLine}
            </p>
          ) : null}
        </div>
      </div>

      <div className="mt-3 space-y-2 rounded-lg border border-border bg-bg-elevated p-3">
        {status.models.length > 0 ? (
          <SettingsSelect
            label={i18nT('pages.developer.strandsSetup.model_label')}
            description={i18nT('pages.developer.strandsSetup.model_desc_default')}
            value={modelDraft || status.models[0]}
            onChange={(val) => {
              setModelDraft(val)
              saveModel.mutate(val)
            }}
            options={status.models}
          />
        ) : (
          <div className="flex flex-wrap items-end gap-2">
            <div className="min-w-0 flex-1">
              <SettingsInput
                label={i18nT('pages.developer.strandsSetup.model_label')}
                description={i18nT('pages.developer.strandsSetup.model_desc_default')}
                value={modelDraft}
                onChange={setModelDraft}
              />
            </div>
            <Btn
              type="button"
              primary
              disabled={!modelDraft.trim() || saveModel.isPending}
              onClick={() => saveModel.mutate(modelDraft.trim())}
            >
              {i18nT('pages.developer.strandsSetup.save')}
            </Btn>
          </div>
        )}
        <ErrorNotice
          message={saveModel.error?.message || null}
          title={i18nT('pages.developer.strandsSetup.save_failed')}
        />
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Btn
          type="button"
          primary={lapsed}
          onClick={onSwitchAccount}
          disabled={logout.isPending}
          data-testid="saokhue-sign-in-switch"
        >
          <RefreshCw className="lucide-inline" aria-hidden="true" />
          {lapsed
            ? i18nT('pages.developer.kiroSignInCard.sign_in_again')
            : i18nT('pages.developer.kiroSignInCard.switch_account')}
        </Btn>
        <Btn
          type="button"
          onClick={() => refreshModels.mutate()}
          disabled={refreshModels.isPending || logout.isPending}
          data-testid="saokhue-sign-in-refresh"
        >
          <RefreshCw
            className={`lucide-inline ${refreshModels.isPending ? 'animate-spin' : ''}`}
            aria-hidden="true"
          />
          {i18nT('pages.developer.agentBackendTab.retry')}
        </Btn>
        <Btn
          type="button"
          danger
          disabled={logout.isPending}
          onClick={() => logout.mutate()}
          data-testid="saokhue-sign-in-logout"
        >
          {logout.isPending ? (
            <Loader2 className="lucide-inline animate-spin" aria-hidden="true" />
          ) : (
            <LogOut className="lucide-inline" aria-hidden="true" />
          )}
          {i18nT('pages.developer.kiroSignInCard.sign_out')}
        </Btn>
      </div>
      <ErrorNotice
        className="mt-3"
        askAgent
        title={i18nT('pages.developer.kiroSignInCard.could_not_sign_out')}
        message={logout.error?.message || null}
        testId="saokhue-sign-in-logout-error"
      />
    </div>
  )
}

function SignInForm({
  defaultBaseUrl,
  onSuccess,
  onCancelReauth,
}: {
  defaultBaseUrl: string
  onSuccess: (status: SaoKhueLoginStatus) => void
  onCancelReauth?: () => void
}) {
  const [tab, setTab] = useState<LoginTab>('sso')
  const [baseUrl, setBaseUrl] = useState(defaultBaseUrl)
  const [token, setToken] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [deviceSession, setDeviceSession] = useState<SaoKhueDeviceSession | null>(null)
  const [deviceError, setDeviceError] = useState('')

  const ssoMut = useMutation({
    mutationFn: () =>
      saokhueLoginApi.loginSso({
        base_url: baseUrl.trim() || undefined,
        open_browser: true,
      }),
    onSuccess: (res) => onSuccess(res),
  })

  const deviceStartMut = useMutation({
    mutationFn: () =>
      saokhueLoginApi.deviceStart({
        base_url: baseUrl.trim() || undefined,
      }),
    onSuccess: (session) => {
      setDeviceError('')
      setDeviceSession(session)
      const targetUrl = session.verification_uri_complete || session.verification_uri
      if (targetUrl) {
        try {
          window.open(targetUrl, '_blank', 'noopener,noreferrer')
        } catch {
          /* ignore popup blocker */
        }
      }
    },
  })

  const tokenMut = useMutation({
    mutationFn: () =>
      saokhueLoginApi.loginToken({
        base_url: baseUrl.trim() || undefined,
        token: token.trim(),
        user_id: username.trim() || undefined,
        verify: true,
      }),
    onSuccess: (res) => {
      setToken('')
      onSuccess(res)
    },
  })

  const passwordMut = useMutation({
    mutationFn: () =>
      saokhueLoginApi.loginPassword({
        base_url: baseUrl.trim() || undefined,
        username: username.trim(),
        password,
      }),
    onSuccess: (res) => {
      setPassword('')
      onSuccess(res)
    },
  })

  useEffect(() => {
    if (!deviceSession) return
    let cancelled = false
    const intervalMs = Math.max(2000, (deviceSession.interval || 5) * 1000)
    const timer = window.setInterval(async () => {
      try {
        const res = await saokhueLoginApi.devicePoll(deviceSession.login_id)
        if (cancelled) return
        if (res.status === 'authorized') {
          window.clearInterval(timer)
          setDeviceSession(null)
          onSuccess(res)
        }
      } catch (err) {
        if (cancelled) return
        window.clearInterval(timer)
        setDeviceError(err instanceof Error ? err.message : String(err))
        setDeviceSession(null)
      }
    }, intervalMs)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [deviceSession, onSuccess])

  const busy =
    ssoMut.isPending ||
    deviceStartMut.isPending ||
    tokenMut.isPending ||
    passwordMut.isPending

  const activeError =
    ssoMut.error?.message ||
    deviceStartMut.error?.message ||
    deviceError ||
    tokenMut.error?.message ||
    passwordMut.error?.message ||
    ''

  return (
    <div className="space-y-3" data-testid="saokhue-sign-in-form">
      <div className="flex flex-wrap gap-2">
        <Btn
          type="button"
          primary={tab === 'sso'}
          disabled={busy}
          onClick={() => {
            setTab('sso')
            setDeviceSession(null)
          }}
          data-testid="saokhue-tab-sso"
        >
          <Building2 className="lucide-inline" aria-hidden="true" />
          {i18nT('components.kasLogin.continue_with_company_sso')}
        </Btn>
        <Btn
          type="button"
          primary={tab === 'device'}
          disabled={busy}
          onClick={() => setTab('device')}
          data-testid="saokhue-tab-device"
        >
          <Link2 className="lucide-inline" aria-hidden="true" />
          {i18nT('components.kasLogin.loopback_use_code')}
        </Btn>
        <Btn
          type="button"
          primary={tab === 'token'}
          disabled={busy}
          onClick={() => {
            setTab('token')
            setDeviceSession(null)
          }}
          data-testid="saokhue-tab-token"
        >
          <KeyRound className="lucide-inline" aria-hidden="true" />
          {i18nT('pages.webhooksPage.token')}
        </Btn>
        <Btn
          type="button"
          primary={tab === 'password'}
          disabled={busy}
          onClick={() => {
            setTab('password')
            setDeviceSession(null)
          }}
          data-testid="saokhue-tab-password"
        >
          {i18nT('pages.hooksPage.user')}
        </Btn>
      </div>

      <SettingsInput
        label={i18nT('pages.mcpGatewayCard.server')}
        value={baseUrl}
        onChange={setBaseUrl}
      />

      {tab === 'sso' && (
        <div className="flex flex-wrap items-center gap-2 pt-1">
          <Btn
            type="button"
            primary
            disabled={busy}
            onClick={() => ssoMut.mutate()}
            data-testid="saokhue-submit-sso"
          >
            {ssoMut.isPending ? (
              <Loader2 className="lucide-inline animate-spin" aria-hidden="true" />
            ) : (
              <ExternalLink className="lucide-inline" aria-hidden="true" />
            )}
            {ssoMut.isPending
              ? i18nT('components.kasLogin.loopback_waiting_title')
              : i18nT('components.kasLogin.continue_with_company_sso')}
          </Btn>
        </div>
      )}

      {tab === 'device' && (
        <div className="space-y-3 pt-1">
          {!deviceSession ? (
            <Btn
              type="button"
              primary
              disabled={busy}
              onClick={() => deviceStartMut.mutate()}
              data-testid="saokhue-submit-device"
            >
              {deviceStartMut.isPending ? (
                <Loader2 className="lucide-inline animate-spin" aria-hidden="true" />
              ) : (
                <Link2 className="lucide-inline" aria-hidden="true" />
              )}
              {i18nT('components.kasLogin.loopback_use_code')}
            </Btn>
          ) : (
            <div
              className="space-y-3 rounded-lg border border-border bg-bg-elevated p-3"
              data-testid="saokhue-device-waiting"
            >
              <div>
                <p className="text-[12px] font-medium text-text">
                  {i18nT('components.kasLogin.open_this_link')}
                </p>
                <CopyCodeField
                  value={deviceSession.verification_uri_complete || deviceSession.verification_uri}
                />
              </div>
              <div>
                <p className="text-[12px] font-medium text-text">
                  {i18nT('components.kasLogin.enter_this_code')}
                </p>
                <div className="mt-1 rounded-lg border border-accent/60 bg-accent-subtle/40 px-4 py-2.5 text-center">
                  <span
                    className="font-mono text-xl font-bold tracking-[0.2em] text-text-strong"
                    data-testid="saokhue-device-user-code"
                  >
                    {deviceSession.user_code}
                  </span>
                </div>
              </div>
              <p className="flex items-center gap-2 text-[12px] text-muted">
                <Loader2 className="lucide-inline animate-spin text-accent" />
                {i18nT('components.kasLogin.waiting_for_you_to_approve')}
              </p>
            </div>
          )}
        </div>
      )}

      {tab === 'token' && (
        <div className="space-y-3 pt-1">
          <SettingsField
            label={i18nT('pages.webhooksPage.token')}
            description={i18nT('pages.webhooksPage.bearer_token_proves_who_is_calling')}
          >
            <Input
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              aria-label={i18nT('pages.webhooksPage.token')}
            />
          </SettingsField>
          <Btn
            type="button"
            primary
            disabled={busy || !token.trim()}
            onClick={() => tokenMut.mutate()}
            data-testid="saokhue-submit-token"
          >
            {tokenMut.isPending ? (
              <Loader2 className="lucide-inline animate-spin" aria-hidden="true" />
            ) : null}
            {i18nT('pages.developer.strandsSetup.save')}
          </Btn>
        </div>
      )}

      {tab === 'password' && (
        <div className="space-y-3 pt-1">
          <SettingsInput
            label={i18nT('pages.hooksPage.user')}
            value={username}
            onChange={setUsername}
          />
          <SettingsField label={i18nT('pages.webhooksPage.token')}>
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              aria-label={i18nT('pages.webhooksPage.token')}
            />
          </SettingsField>
          <Btn
            type="button"
            primary
            disabled={busy || !username.trim() || !password}
            onClick={() => passwordMut.mutate()}
            data-testid="saokhue-submit-password"
          >
            {passwordMut.isPending ? (
              <Loader2 className="lucide-inline animate-spin" aria-hidden="true" />
            ) : null}
            {i18nT('components.kasLogin.sso_continue')}
          </Btn>
        </div>
      )}

      <ErrorNotice
        title={i18nT('components.kasLogin.sign_in_failed')}
        message={activeError || null}
        testId="saokhue-sign-in-error"
      />

      {onCancelReauth && (
        <button
          type="button"
          onClick={onCancelReauth}
          disabled={busy}
          className="mt-2 cursor-pointer text-[13px] text-muted underline-offset-2 hover:text-text hover:underline focus-ring disabled:cursor-not-allowed disabled:opacity-40"
          data-testid="saokhue-sign-in-keep-current"
        >
          {i18nT('pages.developer.kiroSignInCard.keep_current_sign_in')}
        </button>
      )}
    </div>
  )
}

export function SaoKhueSignInCard({ compact = false }: { compact?: boolean } = {}) {
  const queryClient = useQueryClient()
  const title = i18nT('pages.developer.kiroSignInCard.title')
  const [reauthMode, setReauthMode] = useState(false)
  const [signedOutNote, setSignedOutNote] = useState(false)

  const { data: status, isPending, error } = useQuery({
    queryKey: SAOKHUE_SIGN_IN_QUERY_KEY,
    queryFn: () => saokhueLoginApi.status(),
  })

  const handleSuccess = (nextStatus: SaoKhueLoginStatus) => {
    setReauthMode(false)
    setSignedOutNote(false)
    queryClient.setQueryData(SAOKHUE_SIGN_IN_QUERY_KEY, nextStatus)
    clearCachedModels()
    void queryClient.invalidateQueries({ queryKey: ['available-models'] })
    void queryClient.invalidateQueries({ queryKey: ['kiroPrerequisite'] })
  }

  const body = (
    <>
      {signedOutNote ? (
        <p
          className="mb-3 text-[12px] text-muted"
          role="status"
          data-testid="saokhue-sign-in-takes-effect"
        >
          {i18nT('pages.developer.kiroSignInCard.takes_effect_next_process')}
        </p>
      ) : null}
      {isPending ? (
        <p
          className="flex items-center gap-2 text-[13px] text-muted"
          data-testid="saokhue-sign-in-pending"
        >
          <Loader2 className="lucide-inline animate-spin" aria-hidden="true" />
          {i18nT('pages.developer.kiroSignInCard.checking')}
        </p>
      ) : error ? (
        <ErrorNotice
          title={i18nT('components.kasLogin.sign_in_status_unavailable')}
          message={error instanceof Error ? error.message : String(error)}
        />
      ) : status?.authenticated && !reauthMode ? (
        <SignedInView
          status={status}
          onSwitchAccount={() => setReauthMode(true)}
          onSignedOut={() => setSignedOutNote(true)}
        />
      ) : (
        <SignInForm
          defaultBaseUrl={status?.base_url || 'https://portal.aws.rpa.vpbank.dev/api/ai-llm'}
          onSuccess={handleSuccess}
          onCancelReauth={status?.authenticated ? () => setReauthMode(false) : undefined}
        />
      )}
    </>
  )

  if (compact) {
    return (
      <section
        aria-label={title}
        className="border-t border-border pt-3"
        data-setting-key={SAOKHUE_SIGN_IN_ANCHOR}
        data-setting-id={SAOKHUE_SIGN_IN_ANCHOR}
        data-testid="saokhue-sign-in-card"
      >
        <h3 className="mb-2 flex items-center gap-1.5 text-[13px] font-semibold text-text-strong">
          <KeyRound className="lucide-inline" aria-hidden="true" />
          {title}
        </h3>
        {body}
      </section>
    )
  }

  return (
    <Card
      data-setting-key={SAOKHUE_SIGN_IN_ANCHOR}
      data-setting-id={SAOKHUE_SIGN_IN_ANCHOR}
      data-testid="saokhue-sign-in-card"
    >
      <CardTitle>
        <KeyRound className="lucide-inline" aria-hidden="true" />
        {title}
      </CardTitle>
      {body}
    </Card>
  )
}

