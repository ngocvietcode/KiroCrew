import { useId, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import ErrorNotice from '@/components/ErrorNotice'
import { Btn, Input, SendBtn, Toggle } from '@/components/ui'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { i18nT } from '@/i18n/t'
import {
  COMPANY_PROXY_QUERY_KEY,
  companyProxyApi,
  type CompanyProxyAuth,
  type CompanyProxySaveBody,
  type CompanyProxyStatus,
} from './api'
import { proxyHost, testMessage } from './display'
import { k } from './strings'

function errorText(error: unknown): string | null {
  if (!error) return null
  return error instanceof Error ? error.message : String(error)
}

/**
 * The company-proxy form. Opened from the Overview card. A typed password is
 * sent once and stored in the vault by the backend; it is never read back.
 */
export function CompanyProxyDialog({ status, onClose }: { status: CompanyProxyStatus; onClose: () => void }) {
  const queryClient = useQueryClient()
  const id = useId()
  const [enabled, setEnabled] = useState(status.enabled)
  const [auth, setAuth] = useState<CompanyProxyAuth>(status.auth)
  const [url, setUrl] = useState(status.url)
  const [username, setUsername] = useState(status.username)
  const [password, setPassword] = useState('')
  const [clearPassword, setClearPassword] = useState(false)
  const [noProxy, setNoProxy] = useState(status.no_proxy)
  const [saved, setSaved] = useState(false)

  const test = useMutation({
    mutationFn: () =>
      companyProxyApi.test({
        url: url.trim(),
        username: auth === 'windows' ? '' : username.trim(),
        auth,
        ...(password && auth !== 'windows' ? { password } : {}),
      }),
  })
  const save = useMutation({
    mutationFn: () => {
      const body: CompanyProxySaveBody = {
        enabled,
        auth,
        url: url.trim(),
        username: username.trim(),
        no_proxy: noProxy.trim(),
        ...(password ? { password } : {}),
        ...(clearPassword && !password ? { clear_password: true } : {}),
      }
      return companyProxyApi.save(body)
    },
    onSuccess: (next) => {
      queryClient.setQueryData(COMPANY_PROXY_QUERY_KEY, next)
      setPassword('')
      setClearPassword(false)
      setSaved(true)
    },
  })
  const restart = useMutation({ mutationFn: companyProxyApi.restartGateway })

  // An edited field makes an earlier test result and "saved" note stale.
  const edit =
    <T,>(set: (v: T) => void) =>
    (value: T) => {
      set(value)
      test.reset()
      setSaved(false)
    }

  const result = test.data ? testMessage(test.data) : null
  const busy = save.isPending || restart.isPending

  return (
    <Dialog open onOpenChange={(next) => { if (!next && !busy) onClose() }}>
      <DialogContent maxWidth={520}>
        <DialogHeader>
          <DialogTitle>{i18nT(k('dialog_title'))}</DialogTitle>
          <DialogDescription>{i18nT(k('dialog_description'))}</DialogDescription>
        </DialogHeader>
        <DialogBody>
          <div className="flex flex-col gap-3">
            <Toggle checked={enabled} onChange={edit(setEnabled)} label={i18nT(k('field_enabled'))} />

            <label className="flex flex-col gap-1 text-[13px]" htmlFor={`${id}-url`}>
              {i18nT(k('field_url'))}
              <Input
                id={`${id}-url`}
                value={url}
                onChange={(e) => edit(setUrl)(e.target.value)}
                placeholder={i18nT(k('field_url_placeholder'))}
                autoComplete="off"
                spellCheck={false}
              />
            </label>

            <fieldset className="flex flex-col gap-1 text-[13px]">
              <legend className="mb-1">{i18nT(k('field_auth'))}</legend>
              {(['auto', 'windows', 'basic'] as const).map((mode) => (
                <label key={mode} className="flex items-center gap-2">
                  <input
                    type="radio"
                    name={`${id}-auth`}
                    value={mode}
                    checked={auth === mode}
                    disabled={mode === 'windows' && !status.windows_available}
                    onChange={() => edit(setAuth)(mode)}
                  />
                  {i18nT(k(`auth_${mode}`))}
                </label>
              ))}
              {!status.windows_available && (
                <span className="text-[12px] text-muted">{i18nT(k('auth_windows_unavailable'))}</span>
              )}
            </fieldset>

            {auth !== 'windows' && (
            <>
            <label className="flex flex-col gap-1 text-[13px]" htmlFor={`${id}-user`}>
              {i18nT(k('field_username'))}
              <Input
                id={`${id}-user`}
                value={username}
                onChange={(e) => edit(setUsername)(e.target.value)}
                autoComplete="username"
                spellCheck={false}
              />
            </label>

            <label className="flex flex-col gap-1 text-[13px]" htmlFor={`${id}-password`}>
              {i18nT(k('field_password'))}
              <Input
                id={`${id}-password`}
                type="password"
                value={password}
                onChange={(e) => edit(setPassword)(e.target.value)}
                placeholder={status.has_password && !clearPassword ? i18nT(k('field_password_stored')) : ''}
                autoComplete="current-password"
                aria-describedby={`${id}-password-note`}
              />
            </label>
            <div id={`${id}-password-note`} className="flex items-center gap-2 text-[12px] text-muted">
              {status.has_password && !password && !clearPassword && (
                <Btn type="button" onClick={() => edit(setClearPassword)(true)}>
                  {i18nT(k('field_password_remove'))}
                </Btn>
              )}
              {clearPassword && !password && <span>{i18nT(k('field_password_removed'))}</span>}
            </div>
            </>
            )}

            <label className="flex flex-col gap-1 text-[13px]" htmlFor={`${id}-noproxy`}>
              {i18nT(k('field_no_proxy'))}
              <Input
                id={`${id}-noproxy`}
                value={noProxy}
                onChange={(e) => edit(setNoProxy)(e.target.value)}
                placeholder={i18nT(k('field_no_proxy_placeholder'))}
                aria-describedby={`${id}-noproxy-help`}
                spellCheck={false}
              />
            </label>
            <span id={`${id}-noproxy-help`} className="-mt-2 text-[12px] text-muted">
              {i18nT(k('field_no_proxy_help'))}
            </span>

            <div className="flex items-center gap-3">
              <Btn type="button" onClick={() => test.mutate()} disabled={!url.trim() || test.isPending}>
                {test.isPending ? i18nT(k('testing')) : i18nT(k('test'))}
              </Btn>
              <p role="status" aria-live="polite" className={`text-[13px] ${result ? (result.ok ? 'text-accent' : 'text-danger') : ''}`}>
                {result?.text ?? ''}
              </p>
            </div>

            <p className="text-[12px] text-muted">
              {status.running
                ? i18nT(k('status_running'), { port: status.running.port, upstream: proxyHost(status.running.upstream) })
                : status.not_started
                  ? i18nT(k('status_not_started'), { reason: status.not_started })
                  : ''}
            </p>

            {/* No hand-off: the form fields above are an unsaved draft */}
            <ErrorNotice message={errorText(test.error) ?? errorText(save.error) ?? errorText(restart.error)} testId="company-proxy-error" />
            {saved && (
              <p role="status" className="text-[13px] text-accent">
                {i18nT(k('saved_restart'))}
              </p>
            )}
          </div>
        </DialogBody>
        <DialogFooter>
          {saved ? (
            <>
              <Btn type="button" onClick={onClose} disabled={busy}>
                {i18nT(k('close'))}
              </Btn>
              <SendBtn type="button" onClick={() => restart.mutate()} disabled={busy}>
                {restart.isPending ? i18nT(k('restarting')) : i18nT(k('restart_now'))}
              </SendBtn>
            </>
          ) : (
            <>
              <Btn type="button" onClick={onClose} disabled={busy}>
                {i18nT(k('cancel'))}
              </Btn>
              <SendBtn type="button" onClick={() => save.mutate()} disabled={busy}>
                {save.isPending ? i18nT(k('saving')) : i18nT(k('save'))}
              </SendBtn>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
