/**
 * Settings → About section registry.
 *
 * The About card states the identity of this install: the product, its version,
 * the update controls. A downstream edition has more to say about itself (who
 * ships it, what it is built on, where its data lives) and contributes that by
 * registering a component here from its entry module, instead of editing
 * AboutPanel.tsx on every upstream sync.
 *
 * Each registered section is a self-contained component that renders its own
 * markup. AboutPanel renders them directly under the version and licence
 * chips, in `order` (ascending), and hands each one the facts it has already
 * read. The core registers none, so the card is unchanged in the stock build.
 *
 * Registration is expected at module-load time (edition composition), before
 * the page renders — this registry is not reactive.
 */
import type { ComponentType } from 'react'
import { reportSeamCollision } from '../../apps/seamCollision'

/** What AboutPanel has already read, so a section does not read it again. */
export interface AboutSectionContext {
  /** The desktop shell's version, `''` outside the desktop app. */
  appVersion: string
  /** The gateway's version, `''` until the first status push. */
  gatewayVersion: string
  /** True inside the desktop app. */
  isDesktop: boolean
  /** The desktop shell's platform (`win32`, `darwin`, `linux`), `''` outside it. */
  platform: string
  /**
   * Who owns this install's updates when the shell's own updater is off
   * (the baked `EXTERNALLY-MANAGED` marker's `managedBy`); `''` when it is not.
   */
  updatesManagedBy: string
  /** True when the desktop updater is off for any reason. */
  updatesDisabled: boolean
}

export interface AboutSection {
  /** Stable key for React reconciliation + de-dup. */
  id: string
  /** Ascending sort key for placement among registered sections (default 0). */
  order?: number
  /** Self-contained section component. */
  component: ComponentType<{ context: AboutSectionContext }>
}

const ABOUT_SECTIONS: AboutSection[] = []

/**
 * Register one or more About sections. A duplicate `id` is ignored and logs a
 * warning, so re-entrant registration (e.g. HMR) stays idempotent.
 */
export function registerAboutSections(sections: AboutSection[]): void {
  for (const s of sections) {
    if (ABOUT_SECTIONS.some(existing => existing.id === s.id)) {
      reportSeamCollision('aboutSections', `about section ${s.id} already registered; ignoring duplicate`)
      continue
    }
    ABOUT_SECTIONS.push(s)
  }
}

/** All registered About sections, sorted by `order` (ascending, stable). */
export function getAboutSections(): readonly AboutSection[] {
  return [...ABOUT_SECTIONS].sort((a, b) => (a.order ?? 0) - (b.order ?? 0))
}

/** Renders every registered section; nothing at all when there are none. */
export function AboutSections({ context }: { context: AboutSectionContext }) {
  const sections = getAboutSections()
  if (sections.length === 0) return null
  return (
    <>
      {sections.map(({ id, component: Section }) => (
        <Section key={id} context={context} />
      ))}
    </>
  )
}
