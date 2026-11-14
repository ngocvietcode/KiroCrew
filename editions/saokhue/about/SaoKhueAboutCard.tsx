import type { AboutSectionContext } from '@/pages/settings/aboutSections'
import {
  ATTRIBUTION,
  DATA_HOME,
  DATA_HOME_NOTE,
  LABELS,
  LICENSE_NAME,
  NOTICE_NOTE,
  PRODUCT,
  PRODUCT_TAGLINE,
  UPDATES_MANAGED_BY,
  UPDATES_OFF,
  UPSTREAM_BASE_VERSION,
  UPSTREAM_NAME,
  UPSTREAM_NOTICE,
  platformName,
} from './strings'

interface DetailRow {
  id: string
  label: string
  value: string
  note?: string
}

const v = (version: string): string => `v${version}`

/** The rows the card shows for what this install knows about itself. */
export function aboutRows(context: AboutSectionContext): DetailRow[] {
  const rows: DetailRow[] = [{ id: 'product', label: LABELS.product, value: `${PRODUCT} · ${PRODUCT_TAGLINE}` }]
  if (context.isDesktop && context.appVersion) {
    rows.push({ id: 'app-version', label: LABELS.appVersion, value: v(context.appVersion) })
  }
  if (context.gatewayVersion) {
    rows.push({ id: 'gateway-version', label: LABELS.gatewayVersion, value: v(context.gatewayVersion) })
  }
  if (context.platform) {
    rows.push({ id: 'platform', label: LABELS.platform, value: platformName(context.platform) })
  }
  rows.push(
    { id: 'base', label: LABELS.base, value: `${UPSTREAM_NAME} ${v(UPSTREAM_BASE_VERSION)}` },
    { id: 'license', label: LABELS.license, value: LICENSE_NAME },
    { id: 'notice', label: LABELS.notice, value: UPSTREAM_NOTICE, note: NOTICE_NOTE },
    { id: 'data-home', label: LABELS.dataHome, value: DATA_HOME, note: DATA_HOME_NOTE },
  )
  if (context.isDesktop && context.updatesDisabled) {
    rows.push({
      id: 'updates',
      label: LABELS.updates,
      value: context.updatesManagedBy ? UPDATES_MANAGED_BY(context.updatesManagedBy) : UPDATES_OFF,
      note: context.updatesManagedBy ? UPDATES_OFF : undefined,
    })
  }
  return rows
}

/**
 * Settings → About, under the version chips: the product, its versions, what it is
 * built on, its licence and where its data lives. Registered through the About
 * sections seam.
 */
export function SaoKhueAboutCard({ context }: { context: AboutSectionContext }) {
  return (
    <div
      className="mt-3 rounded-lg border border-border bg-bg-elevated px-3 py-2 text-[12px] text-muted leading-relaxed"
      data-testid="about-saokhue"
    >
      <p data-testid="about-fork-attribution">{ATTRIBUTION}</p>
      <dl className="mt-2 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1">
        {aboutRows(context).map((row) => (
          <div key={row.id} className="contents" data-testid={`about-saokhue-${row.id}`}>
            <dt className="text-muted">{row.label}</dt>
            <dd className="min-w-0 break-words font-medium text-text">
              {row.value}
              {row.note && <span className="ml-1.5 font-normal text-muted">({row.note})</span>}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  )
}
