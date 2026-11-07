import { describe, expect, it } from 'vitest'
import { isOverviewBuiltinSuppressed } from '@/pages/overviewBuiltins'
import { getOverviewStatCards } from '@/pages/overviewStatCards'
import hiddenSurfaces from './hiddenSurfaces.css?raw'
import './extensions'

describe('Sao Khuê composition root', () => {
  it('adds the company proxy card', () => {
    expect(getOverviewStatCards().map((c) => c.id)).toContain('saokhue-company-proxy')
  })

  it('suppresses the Overview phone-access card', () => {
    expect(isOverviewBuiltinSuppressed('tailnet-mobile')).toBe(true)
  })

  it('hides the nav rail "Connect your phone" row by its location marker', () => {
    expect(hiddenSurfaces).toMatch(/html \[data-ui-location='shell\.connect-phone'\]\s*\{\s*display: none !important;/)
  })
})
