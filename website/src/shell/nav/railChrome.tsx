import { useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { ArrowLeftToLine, PanelLeft } from 'lucide-react'
import type { getThemeBranding } from '../../themeBranding'
import { i18nT } from '../../i18n/t'
import { useGuideRevealScope } from '../../guide/GuideRevealScope'
import { uiLocation } from '../../uiLocations/uiLocation'
import { OverlayPortal } from '../../components/overlayRoot'
import { useNavTip } from './navTip'
import saokhueStarUrl from '../../assets/saokhue-star.svg'

/** Glyph inside the nav-rail header's expand/collapse button — the same
 *  load-proof contract as MobileNavGlyph, with the rail's own geometry. When
 *  the rail is collapsed the logo is the button's ONLY visible content (the
 *  bot name is unmounted), so a 404 on the avatar asset, a blocked request or
 *  a hung fetch used to leave an invisible control that still toggled the
 *  rail when clicked. A PanelLeft glyph therefore fills the box by default,
 *  the swap to the logo happens only on the img's own `load` event, and
 *  `error` reverts it. `loadedSrc` records WHICH src loaded so a branding or
 *  theme swap falls back until the new asset proves itself. `boxClass` is the
 *  theme-overridable size (`branding.logoClass`, else w-10 collapsed / w-7
 *  expanded) and is applied to BOTH the fallback and the img so the swap never
 *  moves the button's geometry; the hover tilt and `transition-all` classes
 *  live on the img exactly as before. The img stays mounted (display:none)
 *  while hidden so the browser still fetches it. A sibling rather than a
 *  generalisation of MobileNavGlyph: that component's literal `w-6 h-6` box is
 *  pinned by narrowFirstBaseline.test.ts, while this box is a runtime
 *  expression. */
export function RailHeaderGlyph({ avatar, boxClass, iconSize }: { avatar: string; boxClass: string; iconSize: number }) {
  const [loadedSrc, setLoadedSrc] = useState<string | null>(null)
  const showLogo = !!avatar && loadedSrc === avatar
  return (
    <>
      {!showLogo && (
        <span data-testid="rail-header-fallback" className={`${boxClass} flex items-center justify-center shrink-0 transition-all duration-300 group-hover:rotate-[-8deg]`} aria-hidden="true">
          <PanelLeft size={iconSize} />
        </span>
      )}
      {!!avatar && (
        <img src={avatar} alt="" aria-hidden="true" onLoad={() => setLoadedSrc(avatar)} onError={() => setLoadedSrc(null)} className={`${boxClass} rounded-md shrink-0 object-contain transition-all duration-300 group-hover:rotate-[-8deg] ${showLogo ? '' : 'hidden'}`} />
      )}
    </>
  )
}

/** The rail header: the brand mark that is also the collapse toggle. */
export function RailBrandToggle({ effectiveCollapsed, toggleNav, avatar, branding, botName }: {
  effectiveCollapsed: boolean
  toggleNav: () => void
  avatar: string
  branding: ReturnType<typeof getThemeBranding>
  botName: string
}) {
  // The rail's guide scope (`shell.nav-rail`): open while it shows labels.
  useGuideRevealScope('shell.nav-rail', !effectiveCollapsed)
  // Collapsed, the logo is the only expand control and carries no text, so it
  // gets the same instant hover/focus label as the collapsed nav rows (#25743).
  const { tip, tipOn, rowRef, showTip, hideTip, dismissTip, pointerProps } = useNavTip<HTMLButtonElement>(effectiveCollapsed)
  /*
   * mb-1.5 (6px) + the container's gap-0.5 (2px) = 8px between the
   * header and the first nav item, without widening the 2px item gaps.
   */
  return (
    <div className={`relative flex items-center mb-1.5 ${effectiveCollapsed ? 'justify-center' : ''}`}>
      {/* One persistent click target that toggles the rail. The logo
          never unmounts, so there is no swap across collapse/expand; its
          size (w-7 ↔ w-9) and collapsed nudge (mt-1) both animate over
          duration-300, so the mark glides between states rather than
          jumping. Only the brand text + collapse arrow
          animate — fading in on expand and out on collapse via
          AnimatePresence. No hover tint on the row; on hover only the
          logo rotates (group-hover). */}
      {/* No overflow-hidden here: the logo's hover-rotate paints a few
          px past its box, and clipping it looked cut off. Rotation is a
          transform so it doesn't affect the header's layout height
          (row height tracks the logo, collapse-icon alignment
          unchanged); horizontal spill on collapse is still clipped by
          the rail (motion.nav) and the brand text clips itself via
          `truncate`.
          Logo is DUAL-SIZE: w-7 (28px) expanded — 1px card border +
          pt-2 + 14 puts the header row's center on the 23px shared
          control baseline — and w-9 (36px) collapsed, centered in the
          icon strip and nudged down (mt-1) so both its symmetric inset
          and its top clear the rail's rounded-xl corner, which clips
          anything that reaches it; the mt-1 animates (transition-[margin]
          duration-300) in lockstep with the glyph's size transition so
          the flip is a glide, not a jump (a branding logoClass overrides
          both). The collapse arrow pins to top-[6px] rather than centering
          in the row — so its center stays on the
          23px shared control baseline (chat title row, its sessions
          toggle, and the activity strip icons) while the two-line
          brand block makes the row taller. */}
      <button
        type="button"
        className={`group relative flex items-center gap-2 w-full p-0 bg-transparent border-none cursor-pointer text-left ${effectiveCollapsed ? 'justify-center' : ''}`}
        onClick={() => { dismissTip(); toggleNav() }}
        {...pointerProps}
        onMouseEnter={showTip}
        onMouseLeave={hideTip}
        onFocus={showTip}
        onBlur={hideTip}
        title={effectiveCollapsed ? i18nT('app.expand_sidebar') : i18nT('app.collapse_sidebar')}
        aria-label={effectiveCollapsed ? i18nT('app.expand_sidebar') : i18nT('app.collapse_sidebar')}
        aria-expanded={!effectiveCollapsed}
        {...uiLocation('shell.nav-toggle', rowRef)}
      >
        <span className={`flex items-center gap-2.5 min-w-0 transition-[margin] duration-300 ${effectiveCollapsed ? 'mt-1' : ''}`}>
          <RailHeaderGlyph avatar={avatar} boxClass={branding?.logoClass ?? (effectiveCollapsed ? 'w-9 h-9' : 'w-7 h-7')} iconSize={effectiveCollapsed ? 24 : 18} />
          <AnimatePresence initial={false}>
            {!effectiveCollapsed && (
              <motion.span
                key="brand-text"
                initial={{ opacity: 0, x: -6 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -6, transition: { duration: 0.12, ease: 'easeIn' } }}
                transition={{ duration: 0.2, ease: 'easeOut' }}
                className={botName === 'Sao Khuê'
                  ? 'text-[17px] font-black tracking-[-0.04em] leading-none pt-[0.2em] -mt-[0.12em] whitespace-nowrap truncate min-w-0'
                  : 'text-[13px] font-bold tracking-[.14em] uppercase whitespace-nowrap truncate min-w-0'}
              >
                {/* Last word of the bot name carries the accent (KIRO
                    CREW: muted brand, accent product); single-word names
                    render all-muted. Sao Khuê renders the Phương án 4
                    lowercase wordmark with the 4-pointed star on ê. */}
                {botName === 'Sao Khuê' ? (
                  <span aria-label="Sao Khuê" className="inline-flex items-baseline">
                    <span className="text-text-strong">sao</span>
                    <span className="text-accent">
                      khu
                      <span className="relative inline-block">
                        e
                        <img
                          src={saokhueStarUrl}
                          alt=""
                          aria-hidden="true"
                          className="pointer-events-none absolute left-1/2 -top-[0.16em] w-[0.44em] h-[0.44em] -translate-x-1/2"
                        />
                      </span>
                    </span>
                  </span>
                ) : botName.includes(' ') ? (
                  <>
                    <span className="text-muted">{botName.slice(0, botName.lastIndexOf(' ') + 1)}</span>
                    <span className="text-accent/90">{botName.slice(botName.lastIndexOf(' ') + 1)}</span>
                  </>
                ) : (
                  <span className="text-muted">{botName}</span>
                )}
              </motion.span>
            )}
          </AnimatePresence>
        </span>
        {/* Arrow is ABSOLUTE (out of flex flow), pinned to the right.
            If it were a flex child it would reserve ~16px on the right
            from frame 1 of expand — but the rail is still at collapsed
            width (74px) for that frame, so logo + gap + arrow overflowed
            and the logo got crammed/clipped against the arrow (the
            "blink"). Absolute-positioning removes that reserved space, so
            the logo stays put and the arrow just fades in at the edge. */}
        <AnimatePresence initial={false}>
          {!effectiveCollapsed && (
            <motion.span
              key="collapse-arrow"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1, transition: { duration: 0.18, ease: 'easeOut', delay: 0.12 } }}
              exit={{ opacity: 0, transition: { duration: 0.12, ease: 'easeIn' } }}
              className="absolute right-0 top-[6px] h-4 flex items-center text-muted pointer-events-none"
            >
              {/* Arrow-to-edge, not a hide-panel glyph: the rail
                  collapses to an icon rail rather than hiding. */}
              <ArrowLeftToLine size={15} />
            </motion.span>
          )}
        </AnimatePresence>
        {effectiveCollapsed && tip && <OverlayPortal>
          <div
            data-testid="rail-expand-tip"
            className={`fixed flex items-center px-3 rounded-md bg-card border border-border shadow-lg text-text text-sm font-medium z-[9999] pointer-events-none whitespace-nowrap transition-opacity duration-150 ${tipOn ? 'opacity-100' : 'opacity-0'}`}
            style={{ top: tip.top, left: tip.left + tip.width + 4, height: tip.height }}
          >
            {i18nT('app.expand_sidebar')}
          </div>
        </OverlayPortal>}
      </button>
    </div>
  )
}

export function RailCommunityLinks(_props: {
  effectiveCollapsed: boolean
  setReportProblemOpen: (open: boolean) => void
}) {
  return <span className="hidden" {...uiLocation('shell.report-problem')}>{i18nT('app.report_issue')}</span>
}
