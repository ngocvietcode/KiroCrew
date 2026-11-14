import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { AboutSectionContext } from '@/pages/settings/aboutSections'
import { SaoKhueAboutCard, aboutRows } from './SaoKhueAboutCard'

const DESKTOP: AboutSectionContext = {
  appVersion: '0.14.0',
  gatewayVersion: '0.14.0',
  isDesktop: true,
  platform: 'win32',
  updatesManagedBy: 'VPBank IT',
  updatesDisabled: true,
}

const ids = (context: AboutSectionContext) => aboutRows(context).map((r) => r.id)
const text = (testId: string): string => screen.getByTestId(testId).textContent ?? ''

describe('Sao Khuê About card', () => {
  it('says what the product is built on and under which licence', () => {
    render(<SaoKhueAboutCard context={DESKTOP} />)
    expect(text('about-fork-attribution')).toContain('Sao Khuê là bản phát triển kế thừa (fork)')
    expect(text('about-saokhue-base')).toContain('KiroCrew v0.13.0')
    expect(text('about-saokhue-license')).toContain('Apache License 2.0')
    expect(text('about-saokhue-notice')).toContain('Copyright Amazon.com, Inc.')
    expect(text('about-saokhue-data-home')).toContain('~/.vpbank/saokhue')
  })

  it('names both versions, the platform and who ships updates on the desktop app', () => {
    render(<SaoKhueAboutCard context={DESKTOP} />)
    expect(text('about-saokhue-app-version')).toContain('v0.14.0')
    expect(text('about-saokhue-gateway-version')).toContain('v0.14.0')
    expect(text('about-saokhue-platform')).toContain('Windows')
    expect(text('about-saokhue-updates')).toContain('Phân phối bởi VPBank IT')
    expect(text('about-saokhue-updates')).toContain('Tắt kiểm tra cập nhật tự động')
  })

  it('says only that updates are off when no owner is named', () => {
    render(<SaoKhueAboutCard context={{ ...DESKTOP, updatesManagedBy: '' }} />)
    expect(text('about-saokhue-updates')).toMatch(/^Cập nhậtTắt kiểm tra cập nhật tự động$/)
  })

  it('leaves out what a browser tab does not know', () => {
    const web: AboutSectionContext = {
      appVersion: '',
      gatewayVersion: '0.14.0',
      isDesktop: false,
      platform: '',
      updatesManagedBy: '',
      updatesDisabled: false,
    }
    expect(ids(web)).toEqual(['product', 'gateway-version', 'base', 'license', 'notice', 'data-home'])
  })

  it('shows no update row while the updater is on', () => {
    expect(ids({ ...DESKTOP, updatesDisabled: false, updatesManagedBy: '' })).not.toContain('updates')
  })
})
