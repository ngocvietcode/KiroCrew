/**
 * The edition's own catalog, added to i18next at runtime under one top-level key
 * so no core locale file is edited (those carry every core key in twelve
 * languages and conflict on upstream merges). Other languages fall back to
 * English through i18next's fallback.
 */
import { i18next } from '@/i18n'

export const NS = 'saokhueCompanyProxy'

const en = {
  card_label: 'Company proxy',
  card_on: 'On',
  card_off: 'Off',
  card_loading: '…',
  card_restart_needed: 'Restart needed',
  card_not_started: 'Not started',
  card_tooltip_running: 'Through {{host}} as {{user}}',
  card_tooltip_running_no_user: 'Through {{host}}, no sign-in',
  card_tooltip_running_windows: 'Through {{host}} as your Windows account',
  card_tooltip_off: 'Requests go out directly, or through the HTTPS_PROXY the app was started with',
  card_tooltip_restart: 'Saved settings apply after a restart',
  dialog_title: 'Company proxy',
  dialog_description:
    'For networks that reach the internet only through a proxy that asks you to sign in. A local proxy signs in for the gateway and everything it starts, with your Windows account or with a user name and a password kept in the vault.',
  field_enabled: 'Use the company proxy',
  field_auth: 'Sign in with',
  auth_auto: 'Automatic: the user name below if set, otherwise your Windows account',
  auth_windows: 'My Windows account (NTLM or Kerberos, no password stored)',
  auth_basic: 'A user name and password',
  auth_windows_unavailable: 'Signing in with the Windows account works only on Windows.',
  field_url: 'Proxy URL',
  field_url_placeholder: 'http://proxy.example.com:8080',
  field_username: 'User name',
  field_password: 'Password',
  field_password_stored: 'Stored in the vault. Type to replace it.',
  field_password_remove: 'Remove stored password',
  field_password_removed: 'The stored password will be removed when you save.',
  field_no_proxy: 'Reach directly (no proxy)',
  field_no_proxy_placeholder: '.intranet.example.com, .corp.example',
  field_no_proxy_help: 'Comma-separated hosts or domains. localhost is always direct.',
  test: 'Test connection',
  testing: 'Testing…',
  test_ok: 'Connected through the proxy.',
  test_ok_scheme: 'Connected through the proxy ({{scheme}}).',
  test_rejected: 'The proxy rejected the user name or password.',
  test_rejected_no_basic:
    'The proxy does not take a user name and password; it asks for {{schemes}}. Choose "My Windows account" above.',
  test_rejected_windows: 'The proxy rejected the sign-in with your Windows account ({{scheme}}).',
  test_refused: 'The proxy refused the connection (HTTP {{status}}).',
  test_unreachable: 'Cannot reach the proxy. Check the URL and your network.',
  test_timeout: 'The proxy did not answer in time.',
  test_bad_response: 'That address did not answer like an HTTP proxy.',
  status_running: 'Running on 127.0.0.1:{{port}} through {{upstream}}.',
  status_not_started: 'Not started at the last launch: {{reason}}',
  cancel: 'Cancel',
  save: 'Save',
  saving: 'Saving…',
  saved_restart: 'Saved. Restart the gateway to apply it.',
  restart_now: 'Restart now',
  restarting: 'Restarting…',
  close: 'Close',
} as const

export type CompanyProxyKey = keyof typeof en

/** The full i18next key for an edition string. */
export function k(key: CompanyProxyKey): string {
  return `${NS}.${key}`
}

/**
 * Add the catalog once i18next is up. Called from render: the composition root
 * runs before initI18n(), when i18next has no resource store yet.
 */
export function ensureCompanyProxyStrings(): void {
  if (!i18next.isInitialized) return
  if (i18next.exists(k('card_label'), { lng: 'en' })) return
  i18next.addResourceBundle('en', 'translation', { [NS]: en }, true, false)
}

export const COMPANY_PROXY_STRINGS_EN = en
