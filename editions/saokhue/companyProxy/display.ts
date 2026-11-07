/** Pure mappings from API data to what the card and dialog show; unit tested. */
import { i18nT } from '@/i18n/t'
import type { CompanyProxyStatus, CompanyProxyTestResult } from './api'
import { k } from './strings'

export interface CardDisplay {
  value: string
  colorClass: string
  tooltip: string
}

/** The host:port of a proxy URL, for short display; the input when it does not parse. */
export function proxyHost(url: string): string {
  const raw = url.includes('://') ? url : `http://${url}`
  try {
    return new URL(raw).host || url
  } catch {
    return url
  }
}

export function cardDisplay(status: CompanyProxyStatus | undefined): CardDisplay {
  if (!status) return { value: i18nT(k('card_loading')), colorClass: 'text-muted', tooltip: '' }
  if (status.restart_required) {
    return {
      value: i18nT(k('card_restart_needed')),
      colorClass: 'text-warn',
      tooltip: i18nT(k('card_tooltip_restart')),
    }
  }
  if (status.running) {
    const host = proxyHost(status.running.upstream)
    const tooltip =
      status.running.auth === 'windows'
        ? i18nT(k('card_tooltip_running_windows'), { host })
        : status.running.username
          ? i18nT(k('card_tooltip_running'), { host, user: status.running.username })
          : i18nT(k('card_tooltip_running_no_user'), { host })
    return { value: i18nT(k('card_on')), colorClass: 'text-accent', tooltip }
  }
  if (status.enabled && status.not_started) {
    return { value: i18nT(k('card_not_started')), colorClass: 'text-danger', tooltip: status.not_started }
  }
  return { value: i18nT(k('card_off')), colorClass: 'text-muted', tooltip: i18nT(k('card_tooltip_off')) }
}

/** The schemes a Windows-account sign-in speaks. */
const WINDOWS_SCHEMES = new Set(['ntlm', 'negotiate'])

export interface TestMessage {
  ok: boolean
  text: string
}

export function testMessage(result: CompanyProxyTestResult): TestMessage {
  switch (result.outcome) {
    case 'ok':
      return {
        ok: true,
        text: result.scheme ? i18nT(k('test_ok_scheme'), { scheme: result.scheme }) : i18nT(k('test_ok')),
      }
    case 'rejected': {
      if (WINDOWS_SCHEMES.has(result.scheme.toLowerCase())) {
        return { ok: false, text: i18nT(k('test_rejected_windows'), { scheme: result.scheme }) }
      }
      const offersBasic = result.schemes.length === 0 || result.schemes.some((s) => s.toLowerCase() === 'basic')
      return offersBasic
        ? { ok: false, text: i18nT(k('test_rejected')) }
        : { ok: false, text: i18nT(k('test_rejected_no_basic'), { schemes: result.schemes.join(', ') }) }
    }
    case 'refused':
      return { ok: false, text: i18nT(k('test_refused'), { status: result.status }) }
    case 'unreachable':
      return { ok: false, text: i18nT(k('test_unreachable')) }
    case 'timeout':
      return { ok: false, text: i18nT(k('test_timeout')) }
    default:
      return { ok: false, text: i18nT(k('test_bad_response')) }
  }
}
