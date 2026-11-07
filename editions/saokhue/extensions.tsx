/**
 * Sao Khuê edition composition root.
 *
 * Built in when the frontend is compiled with
 *   KIROCREW_EDITION_DIR=<repo>/editions/saokhue KIROCREW_ALLOW_EDITION=1
 * (packaging/build-saokhue.sh sets both). Everything the fork changes in the
 * dashboard registers here through the core's extension seams
 * (website/docs/extension-seams.md), so no core frontend file carries fork code
 * and upstream merges do not conflict on it.
 */
import { suppressOverviewBuiltin } from '@/pages/overviewBuiltins'
import { registerOverviewStatCards } from '@/pages/overviewStatCards'
import { CompanyProxyCard } from './companyProxy/CompanyProxyCard'
import './hiddenSurfaces.css'

registerOverviewStatCards([{ id: 'saokhue-company-proxy', order: 100, component: CompanyProxyCard }])

// Phone access is not offered: the Overview "Phone access" card through its seam,
// and the nav rail's "Connect your phone" row through hiddenSurfaces.css.
suppressOverviewBuiltin('tailnet-mobile')
