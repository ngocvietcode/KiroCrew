/**
 * The Settings → About section seam: an edition registers a section, AboutPanel
 * renders it with the facts it has read, and the stock build renders nothing.
 */
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import {
  AboutSections,
  getAboutSections,
  registerAboutSections,
  type AboutSectionContext,
} from '../pages/settings/aboutSections'

const CONTEXT: AboutSectionContext = {
  appVersion: '1.2.3',
  gatewayVersion: '1.2.4',
  isDesktop: true,
  platform: 'win32',
  updatesManagedBy: 'Example IT',
  updatesDisabled: true,
}

describe('about sections seam', () => {
  it('ships empty, so the stock build draws nothing', () => {
    expect(getAboutSections().filter(s => !s.id.startsWith('test:'))).toEqual([])
    const { container } = render(<AboutSections context={CONTEXT} />)
    expect(container.querySelector('[data-testid^="about-test-"]')).toBeNull()
  })

  it('renders a registered section with the context, in ascending order', () => {
    registerAboutSections([
      { id: 'test:b', order: 2, component: ({ context }) => <p data-testid="about-test-b">{context.updatesManagedBy}</p> },
      { id: 'test:a', order: 1, component: ({ context }) => <p data-testid="about-test-a">{context.appVersion}</p> },
    ])
    expect(getAboutSections().filter(s => s.id.startsWith('test:')).map(s => s.id)).toEqual(['test:a', 'test:b'])
    render(<AboutSections context={CONTEXT} />)
    expect(screen.getByTestId('about-test-a')).toHaveTextContent('1.2.3')
    expect(screen.getByTestId('about-test-b')).toHaveTextContent('Example IT')
  })

  it('refuses a duplicate id loudly and keeps the first registration', () => {
    registerAboutSections([{ id: 'test:dup', component: () => null }])
    expect(() => registerAboutSections([{ id: 'test:dup', component: () => null }])).toThrow()
    expect(getAboutSections().filter(s => s.id === 'test:dup')).toHaveLength(1)
  })
})
