/**
 * Typed API client for Sao Khuê CLI (`saokhue-cli`) interactive dashboard sign-in.
 *
 * Uses the blessed `apiTransport` from `./apiTransport` so every request carries
 * `X-Session-Key`, CSRF origin headers, and runs through the shared `ApiError` /
 * session-recovery pipeline.
 */
import './client'
import { apiTransport } from './apiTransport'

export interface SaoKhueLoginStatus {
  authenticated: boolean
  user_id: string
  email: string
  base_url: string
  auth_method: string
  client_id: string
  default_model: string
  models: string[]
  expires_at: number | null
  expired: boolean
  has_refresh_token: boolean
  auth_path: string
}

export interface SaoKhueDeviceSession {
  ok: boolean
  login_id: string
  user_code: string
  verification_uri: string
  verification_uri_complete: string
  interval: number
  expires_in: number
}

export type SaoKhueDevicePollResult =
  | { ok: boolean; status: 'pending'; slow_down?: boolean }
  | ({ ok: boolean; status: 'authorized' } & SaoKhueLoginStatus)

export const saokhueLoginApi = {
  status: (probe = false): Promise<SaoKhueLoginStatus> => {
    const qs = probe ? '?probe=1' : ''
    return apiTransport.get(`/api/saokhue-login${qs}`).then(apiTransport.j) as Promise<SaoKhueLoginStatus>
  },

  loginSso: (params?: {
    base_url?: string
    default_model?: string
    open_browser?: boolean
  }): Promise<SaoKhueLoginStatus & { ok: boolean }> =>
    apiTransport
      .post('/api/saokhue-login/sso', params ?? {})
      .then(apiTransport.j) as Promise<SaoKhueLoginStatus & { ok: boolean }>,

  deviceStart: (params?: {
    base_url?: string
    default_model?: string
  }): Promise<SaoKhueDeviceSession> =>
    apiTransport
      .post('/api/saokhue-login/device/start', params ?? {})
      .then(apiTransport.j) as Promise<SaoKhueDeviceSession>,

  devicePoll: (login_id: string): Promise<SaoKhueDevicePollResult> =>
    apiTransport
      .post('/api/saokhue-login/device/poll', { login_id })
      .then(apiTransport.j) as Promise<SaoKhueDevicePollResult>,

  loginPassword: (params: {
    base_url?: string
    username: string
    password: string
    default_model?: string
    login_endpoint?: string
  }): Promise<SaoKhueLoginStatus & { ok: boolean }> =>
    apiTransport
      .post('/api/saokhue-login/password', params)
      .then(apiTransport.j) as Promise<SaoKhueLoginStatus & { ok: boolean }>,

  loginToken: (params: {
    base_url?: string
    token: string
    user_id?: string
    default_model?: string
    verify?: boolean
  }): Promise<SaoKhueLoginStatus & { ok: boolean }> =>
    apiTransport
      .post('/api/saokhue-login/token', params)
      .then(apiTransport.j) as Promise<SaoKhueLoginStatus & { ok: boolean }>,

  setModel: (default_model: string): Promise<SaoKhueLoginStatus & { ok: boolean }> =>
    apiTransport
      .post('/api/saokhue-login/model', { default_model })
      .then(apiTransport.j) as Promise<SaoKhueLoginStatus & { ok: boolean }>,

  logout: (): Promise<SaoKhueLoginStatus & { ok: boolean; removed: boolean }> =>
    apiTransport
      .post('/api/saokhue-login/logout', {})
      .then(apiTransport.j) as Promise<SaoKhueLoginStatus & { ok: boolean; removed: boolean }>,
}

