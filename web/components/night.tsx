// Decorative pieces of the v3 "night" look: the hero mosaic and the now-loaded ticker.

const TILE_COLOURS = ["bg-plum", "bg-wine", "bg-raised", "bg-plum/80", "bg-wine/70"];
// Lit tiles: [index, colour]. Fixed so the server and browser render the same floor.
const LIT: Record<number, string> = {
  4: "bg-pink",
  9: "bg-cream",
  12: "bg-pink-soft",
  13: "bg-[#d9d4d6]",
  20: "bg-pink",
  24: "bg-cream",
  29: "bg-pink",
  35: "bg-cream",
  38: "bg-pink",
};

/** A tilted dance floor of 7×6 tiles; a few are lit and pulse in turn. */
export function Mosaic({ className = "" }: { className?: string }) {
  return (
    <div className={`mosaic grid grid-cols-7 gap-1 ${className}`} aria-hidden="true">
      {Array.from({ length: 42 }, (_, i) => (
        <span
          key={i}
          className={`aspect-square rounded-[2px] ${LIT[i] ?? TILE_COLOURS[(i * 7 + (i >> 2)) % TILE_COLOURS.length]}`}
          style={{ animationDelay: `${((i * 13) % 42) * 0.15}s` }}
        />
      ))}
    </div>
  );
}

/** LED-style marquee: "NOW LOADED ▸ song ▸ song …". Static text for screen readers. */
export function Ticker({ items, empty }: { items: string[]; empty: string }) {
  const text = items.length ? `Now loaded ▸ ${items.join(" ▸ ")}` : empty;
  // Short text is repeated so the strip never runs half empty.
  const loop = Array(Math.max(1, Math.ceil(90 / (text.length + 6)))).fill(text).join("  ···  ");
  // Two copies side by side; the track slides by half its width, so the loop is seamless.
  const run = () => (
    <span className="whitespace-nowrap pr-16">
      {loop} <span className="text-pink/50">···</span>
    </span>
  );
  return (
    <div className="overflow-hidden rounded-md border border-line bg-panel px-3 py-3">
      <span className="sr-only">{text}</span>
      <div className="ticker-track ticker-glow font-mono text-sm uppercase tracking-[0.12em] text-pink sm:text-base" aria-hidden="true">
        {run()}
        {run()}
      </div>
    </div>
  );
}
