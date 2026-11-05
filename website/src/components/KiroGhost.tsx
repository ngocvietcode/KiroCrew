interface KiroGhostProps {
  size?: number
  className?: string
}

/** The Sao Khuê (VPBank) emblem — Bông Hoa Thịnh Vượng VPBank. */
export function KiroGhost({ size = 40, className = '' }: KiroGhostProps) {
  return (
    <svg width={size} height={size} viewBox="0 -22 272 272" fill="none" className={className}>
      <g fill="#E50514">
        {/* Left Upper-Inner Petal */}
        <path d="M 73 4.5 C 73 0 75.5 -0.8 78.5 1.8 L 132.5 62 L 132.5 150.5 L 82.5 92.5 C 75.5 84.5 73 76 73 66 Z" />
        {/* Right Upper-Inner Petal */}
        <path d="M 199 4.5 C 199 0 196.5 -0.8 193.5 1.8 L 139.5 62 L 139.5 150.5 L 189.5 92.5 C 196.5 84.5 199 76 199 66 Z" />
        {/* Left Outer-Lower Petal */}
        <path d="M 3.5 84 C -0.5 84 -1 88.5 1.8 92 L 112 219.5 C 118 225.5 125 228 132.5 228 L 132.5 163.5 L 79.5 101.5 C 67 87.5 52 84 32 84 Z" />
        {/* Right Outer-Lower Petal */}
        <path d="M 268.5 84 C 272.5 84 273 88.5 270.2 92 L 160 219.5 C 154 225.5 147 228 139.5 228 L 139.5 163.5 L 192.5 101.5 C 205 87.5 220 84 240 84 Z" />
      </g>
    </svg>
  )
}
