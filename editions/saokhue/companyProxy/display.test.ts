import { beforeAll, describe, expect, it } from 'vitest'
import type { CompanyProxyStatus, CompanyProxyTestResult } from './api'
import { cardDisplay, proxyHost, testMessage } from './display'
import { ensureCompanyProxyStrings } from './strings'

const base: CompanyProxyStatus = {
  enabled: false,
  url: '',
  username: '',
  no_proxy: '',
  has_password: false,
  auth: 'auto',
  windows_available: true,
  restart_required: false,
  running: null,
  not_started: '',
}

function result(fields: Partial<CompanyProxyTestResult>): CompanyProxyTestResult {
  return { ok: false, outcome: 'bad_response', status: 0, schemes: [], scheme: '', ...fields }
}

beforeAll(() => ensureCompanyProxyStrings())

describe('proxyHost', () => {
  it('shows host:port and tolerates a bare host or junk', () => {
    expect(proxyHost('http://proxynorth.corp.test:8080')).toBe('proxynorth.corp.test:8080')
    expect(proxyHost('proxy.corp.test:3128')).toBe('proxy.corp.test:3128')
    expect(proxyHost('::not a url::')).toBe('::not a url::')
  })
})

describe('cardDisplay', () => {
  it('reads as loading before the first answer', () => {
    expect(cardDisplay(undefined).value).toBe('…')
  })

  it('is Off when nothing is enabled', () => {
    expect(cardDisplay(base)).toMatchObject({ value: 'Off', colorClass: 'text-muted' })
  })

  it('names the upstream and user while running on Basic', () => {
    const shown = cardDisplay({
      ...base,
      enabled: true,
      running: { port: 4242, upstream: 'http://proxynorth.corp.test:8080', username: 'alice', no_proxy: '', auth: 'basic' },
    })
    expect(shown).toEqual({
      value: 'On',
      colorClass: 'text-accent',
      tooltip: 'Through proxynorth.corp.test:8080 as alice',
    })
  })

  it('says the Windows account signs in while running on Windows sign-in', () => {
    const shown = cardDisplay({
      ...base,
      enabled: true,
      running: { port: 4242, upstream: 'http://proxynorth.corp.test:8080', username: '', no_proxy: '', auth: 'windows' },
    })
    expect(shown.tooltip).toBe('Through proxynorth.corp.test:8080 as your Windows account')
  })

  it('shows why the proxy did not start', () => {
    const shown = cardDisplay({ ...base, enabled: true, not_started: 'no company proxy is set' })
    expect(shown).toEqual({ value: 'Not started', colorClass: 'text-danger', tooltip: 'no company proxy is set' })
  })

  it('asks for a restart after a save, whatever runs now', () => {
    const shown = cardDisplay({
      ...base,
      restart_required: true,
      running: { port: 1, upstream: 'http://a:1', username: '', no_proxy: '', auth: 'none' },
    })
    expect(shown).toMatchObject({ value: 'Restart needed', colorClass: 'text-warn' })
  })
})

describe('testMessage', () => {
  it.each([
    [result({ ok: true, outcome: 'ok', status: 200 }), true, 'Connected through the proxy.'],
    [result({ ok: true, outcome: 'ok', status: 200, scheme: 'Negotiate' }), true, 'Connected through the proxy (Negotiate).'],
    [result({ outcome: 'rejected', status: 407, schemes: ['Basic'], scheme: 'Basic' }), false, 'The proxy rejected the user name or password.'],
    [
      result({ outcome: 'rejected', status: 407, schemes: ['NTLM'], scheme: 'NTLM' }),
      false,
      'The proxy rejected the sign-in with your Windows account (NTLM).',
    ],
    [result({ outcome: 'refused', status: 403 }), false, 'The proxy refused the connection (HTTP 403).'],
    [result({ outcome: 'unreachable' }), false, 'Cannot reach the proxy. Check the URL and your network.'],
    [result({ outcome: 'timeout' }), false, 'The proxy did not answer in time.'],
    [result({ outcome: 'bad_response' }), false, 'That address did not answer like an HTTP proxy.'],
  ])('%o', (given, ok, text) => {
    expect(testMessage(given)).toEqual({ ok, text })
  })

  it('suggests the Windows account when the proxy does not take Basic', () => {
    expect(testMessage(result({ outcome: 'rejected', status: 407, schemes: ['NTLM', 'Negotiate'], scheme: 'Basic' })).text).toBe(
      'The proxy does not take a user name and password; it asks for NTLM, Negotiate. Choose "My Windows account" above.',
    )
  })
})
