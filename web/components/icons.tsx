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

export const Spinner = ({ className = base }: P) => (
  <svg viewBox="0 0 24 24" fill="none" className={`${className} animate-spin`} aria-hidden="true">
    <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity={0.25} strokeWidth={3} />
    <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth={3} strokeLinecap="round" />
  </svg>
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
