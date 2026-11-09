/**
 * VPBank Prosperity Flower, standing in for the 8 mascot poses of the chat
 * loading carousel (the carousel keeps its 8 slots so its cross-fade is unchanged).
 *
 * The source art lives in `assets/ghost-poses/pose-N.svg` and is rendered through
 * a plain `<img>` via a default URL import — NOT svgr/`?react`, and with no `<svg>`
 * element or path data in this (or any) TSX file. That is what the `use-lucide-icons`
 * rule requires of a brand mark: lucide-react ships no mascot glyphs, so the art is
 * exempt from "use lucide-react" ONLY while it stays an asset rather than inline
 * code. Same pattern as `assets/onboarding/GhostIcons.tsx`.
 *
 * The asset is a fixed white-body / black-eyes silhouette; theming is applied to the
 * `<img>` from CSS (see `.kpc` in index.css), where the light palette traces a black
 * outline with a `drop-shadow()` filter chain so the white body reads against a pale
 * surface. A CSS filter follows the image's rendered alpha, so no per-theme asset
 * variant is needed.
 */
import flowerUrl from '../assets/vpbank-flower-pose.svg'

/** Pose art URLs, in design-system order. */
export const GHOST_POSE_URLS: string[] = Array.from({ length: 8 }, () => flowerUrl)

/** One pose, sized by the carousel. Decorative: no alt text, aria-hidden. */
export function GhostPose({ src }: { src: string }) {
  return <img className="kp" src={src} alt="" aria-hidden="true" draggable={false} />
}

/**
 * The poses as loader-carousel icons — the shape `ThemeBranding.loaderIcons`
 * expects. Safe to swap between: the carousel animates a persistent wrapper, so
 * remounting the inner element does not restart the cross-fade.
 */
export const GHOST_POSE_ICONS = GHOST_POSE_URLS.map(
  src => function GhostPoseIcon() { return <GhostPose src={src} /> }
)
