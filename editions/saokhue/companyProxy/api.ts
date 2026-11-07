/**
 * Company-proxy API, built on the core's exported transport (the edition API seam,
 * website/docs/extension-seams.md "API methods"): the session header and the
 * ApiError pipeline come with it, and no core client file is touched.
 */
import { apiTransport as t } from '@/api/apiTransport'

export type CompanyProxyAuth = 'auto' | 'basic' | 'windows'

export interface CompanyProxyRunning {
  port: number
  upstream: string
  username: string
  no_proxy: string
  /** How the running proxy signs in: basic, windows or none. */
  auth: 'basic' | 'windows' | 'none'
}

/** GET /api/network/proxy. The password itself is never returned. */
export interface CompanyProxyStatus {
  enabled: boolean
  url: string
  username: string
  no_proxy: string
  has_password: boolean
  auth: CompanyProxyAuth
  /** Whether this machine can sign in with its Windows account. */
  windows_available: boolean
  /** Settings were saved after this gateway started; they apply after a restart. */
  restart_required: boolean
  /** What this gateway started the proxy with, or null when none serves. */
  running: CompanyProxyRunning | null
  /** Why the proxy did not start at boot; empty when it is simply off. */
  not_started: string
}

export interface CompanyProxySaveBody {
  enabled: boolean
  auth: CompanyProxyAuth
  url: string
  username: string
  no_proxy: string
  /** Sent only when the user typed one; it is stored in the vault. */
  password?: string
  clear_password?: boolean
}

export type CompanyProxyOutcome = 'ok' | 'rejected' | 'refused' | 'unreachable' | 'timeout' | 'bad_response'

export interface CompanyProxyTestResult {
  ok: boolean
  outcome: CompanyProxyOutcome
  status: number
  /** Schemes a rejecting proxy offers, e.g. ["NTLM"]. */
  schemes: string[]
  /** How the sign-in went through: Basic, NTLM, Negotiate, or empty. */
  scheme: string
}

export const COMPANY_PROXY_QUERY_KEY = ['saokhue', 'company-proxy'] as const

export const companyProxyApi = {
  status: () => t.get('/api/network/proxy').then(t.j) as Promise<CompanyProxyStatus>,
  save: (body: CompanyProxySaveBody) =>
    t.post('/api/network/proxy', body).then(t.j) as Promise<CompanyProxyStatus>,
  test: (body: { url: string; username: string; auth: CompanyProxyAuth; password?: string }) =>
    t.post('/api/network/proxy/test', body).then(t.j) as Promise<CompanyProxyTestResult>,
  restartGateway: () => t.post('/api/restart').then(t.j),
}
