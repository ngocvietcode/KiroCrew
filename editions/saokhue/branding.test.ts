import { describe, expect, it } from 'vitest'
import { i18next, registerCatalogs } from '@/i18n'
import { installBranding, rebrandValue } from './branding'

installBranding()

describe('Sao Khuê catalog wording', () => {
  it('respells the product name, the data home and its variable', () => {
    expect(rebrandValue('any.key', 'Restart Kiro Crew')).toBe('Restart Sao Khuê')
    expect(rebrandValue('any.key', '$KIROCREW_HOME/config.json (default ~/.kiro/crew/config.json)')).toBe(
      '$SAOKHUE_HOME/config.json (default ~/.vpbank/saokhue/config.json)',
    )
  })

  it('leaves the joined spelling alone, so header and app names stay valid', () => {
    expect(rebrandValue('any.key', 'send X-KiroCrew-Timestamp')).toBe('send X-KiroCrew-Timestamp')
  })

  it('renames the bot only in the keys that name it', () => {
    expect(rebrandValue('components.onboardingFlow.tell_kiro_about_you', 'Tell Kiro about you')).toBe(
      'Tell Sao Khuê about you',
    )
    expect(rebrandValue('any.key', 'Sign in to Kiro')).toBe('Sign in to Kiro')
  })

  it('words the English catalog that was registered before it was installed', () => {
    expect(i18next.t('components.onboardingFlow.tell_kiro_about_you')).toBe('Tell Sao Khuê about you')
    expect(i18next.t('pages.settings.aboutPanel.open_source_under_the_apache_2_0_license')).toContain('Apache 2.0')
    expect(i18next.t('pages.settings.aboutPanel.open_source_under_the_apache_2_0_license')).toContain('KiroCrew')
  })

  it('words a catalog registered later', () => {
    registerCatalogs({ de: { translation: { editionTest: { restart: 'Kiro Crew neu starten' } } } })
    expect(i18next.getResource('de', 'translation', 'editionTest.restart')).toBe('Sao Khuê neu starten')
    expect(i18next.getResource('de', 'translation', 'pages.settings.aboutPanel.open_source_under_the_apache_2_0_license')).toContain('Apache 2.0')
  })
})
