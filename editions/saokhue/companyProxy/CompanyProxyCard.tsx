import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { StatCard } from '@/components/ui'
import { i18nT } from '@/i18n/t'
import { COMPANY_PROXY_QUERY_KEY, companyProxyApi } from './api'
import { CompanyProxyDialog } from './CompanyProxyDialog'
import { cardDisplay } from './display'
import { ensureCompanyProxyStrings, k } from './strings'

/**
 * Settings → Overview tile for the company proxy, registered through the
 * Overview status-card seam. Clicking it opens the settings dialog. The status
 * endpoint is owner only, so a non-owner (403) sees no tile at all.
 */
export function CompanyProxyCard({ delay }: { delay?: number }) {
  ensureCompanyProxyStrings()
  const [open, setOpen] = useState(false)
  const { data, isError } = useQuery({
    queryKey: COMPANY_PROXY_QUERY_KEY,
    queryFn: companyProxyApi.status,
    retry: false,
  })
  if (isError) return null
  const display = cardDisplay(data)
  return (
    <>
      <StatCard
        label={i18nT(k('card_label'))}
        value={display.value}
        colorClass={display.colorClass}
        title={display.tooltip}
        delay={delay}
        onClick={data ? () => setOpen(true) : undefined}
        data-testid="company-proxy-card"
      />
      {open && data && <CompanyProxyDialog status={data} onClose={() => setOpen(false)} />}
    </>
  )
}
