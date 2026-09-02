// Stroke icons on a 24px grid, sized by the parent via font-size / explicit props. No emoji.

type IconProps = { size?: number; className?: string };

const base = (size: number) => ({
  width: size,
  height: size,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 2.2,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  'aria-hidden': true,
});

export function IconPlay({ size = 12, className }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" className={className} aria-hidden>
      <path d="M6 4l14 8-14 8z" />
    </svg>
  );
}

export function IconArrow({ size = 14, className }: IconProps) {
  return <svg {...base(size)} className={className}><path d="M5 12h14M13 6l6 6-6 6" /></svg>;
}

export function IconCheck({ size = 16, className }: IconProps) {
  return (
    <svg {...base(size)} className={className}>
      <circle cx="12" cy="12" r="9" />
      <path d="M8 12.5l2.8 2.8L16.5 9.5" />
    </svg>
  );
}

export function IconAlert({ size = 16, className }: IconProps) {
  return (
    <svg {...base(size)} className={className}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7.5v5.5M12 16.5v.2" />
    </svg>
  );
}

export function IconRefresh({ size = 15, className }: IconProps) {
  return (
    <svg {...base(size)} strokeWidth={2} className={className}>
      <path d="M4 9a8 8 0 0 1 14-3l2 2M20 4v4h-4M20 15a8 8 0 0 1-14 3l-2-2M4 20v-4h4" />
    </svg>
  );
}

export function IconClose({ size = 14, className }: IconProps) {
  return <svg {...base(size)} className={className}><path d="M6 6l12 12M18 6L6 18" /></svg>;
}

export function IconUser({ size = 14, className }: IconProps) {
  return (
    <svg {...base(size)} strokeWidth={2} className={className}>
      <circle cx="12" cy="8" r="4" />
      <path d="M4 21c0-4 3.6-7 8-7s8 3 8 7" />
    </svg>
  );
}

export function IconUpload({ size = 15, className }: IconProps) {
  return <svg {...base(size)} strokeWidth={2} className={className}><path d="M12 16V4M6 10l6-6 6 6M4 20h16" /></svg>;
}

/** Spinning arc; the rotation lives in games.css (.games-spin). */
export function IconSpinner({ size = 14, className }: IconProps) {
  return (
    <svg {...base(size)} className={`games-spin${className ? ` ${className}` : ''}`}>
      <path d="M12 3a9 9 0 1 1-6.4 2.6" />
    </svg>
  );
}
