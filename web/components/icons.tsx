// Small inline icons (no icon library needed).

type P = { className?: string };
const base = "h-4 w-4";

export const PlayIcon = ({ className = base }: P) => (
  <svg viewBox="0 0 20 20" fill="currentColor" className={className} aria-hidden="true">
    <path d="M6.3 3.7A1 1 0 0 0 5 4.6v10.8a1 1 0 0 0 1.5.9l8.8-5.4a1 1 0 0 0 0-1.8L6.3 3.7Z" />
  </svg>
);

export const PauseIcon = ({ className = base }: P) => (
  <svg viewBox="0 0 20 20" fill="currentColor" className={className} aria-hidden="true">
    <path d="M5 4h3.5v12H5zM11.5 4H15v12h-3.5z" />
  </svg>
);

export const CheckIcon = ({ className = base }: P) => (
  <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth={2.2} className={className} aria-hidden="true">
    <path d="m4.5 10.5 3.5 3.5 7.5-8" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

export const PlusIcon = ({ className = base }: P) => (
  <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth={2.2} className={className} aria-hidden="true">
    <path d="M10 4v12M4 10h12" strokeLinecap="round" />
  </svg>
);

export const XIcon = ({ className = base }: P) => (
  <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth={2.2} className={className} aria-hidden="true">
    <path d="m5 5 10 10M15 5 5 15" strokeLinecap="round" />
  </svg>
);

/** Small loader: level bars bouncing out of phase (CSS `.loader`; a slow fade for reduced motion). */
export const Spinner = ({ className = base }: P) => (
  <span className={`loader inline-flex items-end justify-center gap-[2px] align-middle ${className}`} aria-hidden="true">
    <span className="h-full w-[3px] rounded-[1px] bg-current" />
    <span className="h-full w-[3px] rounded-[1px] bg-current" />
    <span className="h-full w-[3px] rounded-[1px] bg-current" />
  </span>
);

/** Swave logo: four rising bars. */
export const LogoIcon = ({ className = base }: P) => (
  <svg viewBox="0 0 20 20" fill="currentColor" className={className} aria-hidden="true">
    <rect x="2" y="11" width="3" height="7" rx="0.5" />
    <rect x="6.5" y="7" width="3" height="11" rx="0.5" opacity="0.85" />
    <rect x="11" y="3" width="3" height="15" rx="0.5" opacity="0.7" />
    <rect x="15.5" y="9" width="3" height="9" rx="0.5" opacity="0.55" />
  </svg>
);

/** Three level bars; they bounce while `playing` (CSS `.eq`, off for reduced motion). */
export const Bars = ({ playing = false, className = base }: P & { playing?: boolean }) => (
  <span className={`${playing ? "eq" : ""} flex items-end gap-[2px] ${className}`} aria-hidden="true">
    <span className="h-[55%] w-[3px] rounded-[1px] bg-current" />
    <span className="h-full w-[3px] rounded-[1px] bg-current" />
    <span className="h-[75%] w-[3px] rounded-[1px] bg-current" />
  </span>
);

export const SunIcon = ({ className = base }: P) => (
  <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth={1.8} className={className} aria-hidden="true">
    <circle cx="10" cy="10" r="3.5" />
    <path d="M10 1.5v2.2M10 16.3v2.2M1.5 10h2.2M16.3 10h2.2M4 4l1.6 1.6M14.4 14.4 16 16M4 16l1.6-1.6M14.4 5.6 16 4" strokeLinecap="round" />
  </svg>
);

export const MoonIcon = ({ className = base }: P) => (
  <svg viewBox="0 0 20 20" fill="currentColor" className={className} aria-hidden="true">
    <path d="M15.8 12.6A7 7 0 0 1 7.4 4.2a.6.6 0 0 0-.8-.7 7.5 7.5 0 1 0 9.9 9.9.6.6 0 0 0-.7-.8Z" />
  </svg>
);

/** Arrow out of a box: opens elsewhere. */
export const ExternalIcon = ({ className = base }: P) => (
  <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth={1.9} className={className} aria-hidden="true">
    <path d="M11 4h5v5M16 4l-7 7M9 5H5v10h10v-4" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);
