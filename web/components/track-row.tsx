import type { Recommendation } from "@/lib/api";
import type { PreviewInfo } from "@/lib/use-preview";
import { Bars, ExternalIcon, PauseIcon, PlayIcon, PlusIcon, Spinner, XIcon } from "./icons";

export type PreviewState = "idle" | "loading" | "playing" | "unavailable";

type Props = {
  track: Recommendation;
  position: number;
  kept: boolean;
  pending: boolean;
  preview: PreviewState;
  canAddSeed: boolean;
  onPreview: () => void;
  onKeep: () => void;
  onMore: () => void;
  onRemove: () => void;
  /** Where the preview comes from (iTunes previews must link back to Apple Music). */
  previewInfo?: PreviewInfo;
  /** Open the track on the user's service when it has no preview. */
  open?: { href: string; label: string };
  /** "source": this song is being mixed in as a seed; "new": it just arrived from a mix. */
  mix?: "source" | "new";
  /** Order among the newly arrived songs, to stagger their entrance. */
  mixOrder?: number;
  /** Album cover URL; undefined while loading, null when there is none. */
  cover?: string | null;
};

const focus = "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-pink";
const chip = `btn-label rounded-md border px-2.5 py-2 transition disabled:cursor-not-allowed disabled:opacity-30 ${focus}`;

export default function TrackRow(p: Props) {
  const { track: t } = p;
  return (
    <li
      className={`flex items-center gap-3 border-b border-line/70 px-2 py-2.5 transition sm:px-3 ${
        p.kept ? "bg-wine/45" : "hover:bg-raised/60"
      } ${p.pending ? "opacity-40" : ""} ${p.mix === "source" ? "mix-source" : p.mix === "new" ? "mix-new" : ""}`}
      style={p.mix === "new" ? { animationDelay: `${(p.mixOrder ?? 0) * 70}ms` } : undefined}
    >
      <span className="label hidden w-6 shrink-0 text-pink/70 tabular-nums sm:block">
        {String(p.position + 1).padStart(2, "0")}
      </span>

      <CoverButton
        uri={t.track_uri}
        name={t.name}
        cover={p.cover}
        state={p.pending ? "loading" : p.preview}
        disabled={p.pending}
        onClick={p.onPreview}
        open={p.open}
      />

      <div className="min-w-0 flex-1">
        <p className="flex items-center gap-2 text-sm font-semibold">
          <span className="truncate">{t.name}</span>
          {p.mix === "new" && (
            <span className="label shrink-0 rounded-sm border border-pink/60 px-1 text-[0.6rem] leading-4 text-pink">New</span>
          )}
        </p>
        <p className="truncate text-xs text-muted">
          {p.previewInfo?.source === "itunes" && p.previewInfo.open_url && (
            <a
              href={p.previewInfo.open_url}
              target="_blank"
              rel="noreferrer"
              className="mr-1.5 rounded-sm border border-line px-1 text-[0.65rem] text-dim hover:text-text"
              title="Preview provided by Apple Music"
            >
              Apple Music ↗
            </a>
          )}
          <span title={t.reason || undefined}>{t.artist}</span>
        </p>
      </div>

      <div className="flex shrink-0 items-center gap-1.5">
        <button
          onClick={p.onMore}
          disabled={p.pending || !p.canAddSeed}
          className={`${chip} border-line text-text hover:border-pink/60 hover:text-pink-soft`}
          aria-label={`More like ${t.name}`}
          title={p.canAddSeed ? "More like this (adds it as a seed)" : "Seed limit reached"}
        >
          {p.mix === "source" ? (
            <span className="flex items-center gap-1.5 text-pink">
              <Spinner className="h-3 w-3.5" />
              <span className="hidden sm:inline">Mixing</span>
            </span>
          ) : (
            <>
              <span className="hidden sm:inline">More like this</span>
              <PlusIcon className="h-3.5 w-3.5 sm:hidden" />
            </>
          )}
        </button>
        <button
          onClick={p.onKeep}
          disabled={p.pending}
          aria-pressed={p.kept}
          className={`${chip} min-w-14 ${
            p.kept ? "border-cream bg-cream text-ink hover:bg-cream/85" : "border-cream/50 text-cream hover:bg-cream/10"
          }`}
          aria-label={p.kept ? `Un-keep ${t.name}` : `Keep ${t.name}`}
        >
          {p.kept ? "Kept" : "Keep"}
        </button>
        <button
          onClick={p.onRemove}
          disabled={p.pending}
          className={`rounded-md p-2 text-muted hover:bg-raised hover:text-text disabled:opacity-30 ${focus}`}
          aria-label={`Remove ${t.name}`}
          title="Remove (replace with the next best)"
        >
          <XIcon className="h-3.5 w-3.5" />
        </button>
      </div>
    </li>
  );
}

/** The album cover is the play button: a play icon shows on hover/focus, bars while playing.
 *  No preview anywhere → the cover (dimmed) links to the song on the user's service instead. */
export function CoverButton(p: {
  uri: string;
  name: string;
  cover?: string | null;
  state: PreviewState;
  disabled?: boolean;
  onClick: () => void;
  open?: { href: string; label: string };
}) {
  const playing = p.state === "playing";
  const unavailable = p.state === "unavailable";
  const open = p.open ?? { href: spotifyUrl(p.uri), label: "Spotify" };
  const base = `group relative h-10 w-10 shrink-0 overflow-hidden rounded-md border transition ${
    playing ? "border-pink ring-2 ring-pink/40" : "border-line hover:border-pink/60"
  } ${focus}`;
  // the overlay: always visible while loading/playing, otherwise only on hover/focus
  const shown = p.state === "loading" || playing;
  const overlay = `absolute inset-0 grid place-items-center bg-ink/60 text-text transition-opacity ${
    shown ? "opacity-100" : "opacity-0 group-hover:opacity-100 group-focus-visible:opacity-100"
  }`;
  const art = p.cover ? (
    // eslint-disable-next-line @next/next/no-img-element
    <img src={p.cover} alt="" width={40} height={40} loading="lazy" decoding="async"
         className={`h-full w-full object-cover ${unavailable ? "opacity-50 grayscale" : ""}`} />
  ) : (
    <span aria-hidden className="grid h-full w-full place-items-center bg-raised text-dim">
      {!unavailable && <Bars className="h-3.5" />}
    </span>
  );
  if (unavailable) {
    return (
      <a href={open.href} target="_blank" rel="noreferrer" className={base}
         aria-label={`No preview for ${p.name}: open it in ${open.label}`}
         title={`No preview available: open in ${open.label}`}>
        {art}
        <span className={overlay}><ExternalIcon className="h-3.5 w-3.5" /></span>
      </a>
    );
  }
  return (
    <button type="button" onClick={p.onClick} disabled={p.disabled}
            className={`${base} cursor-pointer disabled:cursor-not-allowed`}
            aria-label={playing ? `Pause ${p.name}` : `Play preview of ${p.name}`}
            title={playing ? "Pause" : "Play 30-second preview"}>
      {art}
      <span className={overlay}>
        {p.state === "loading" ? (
          <Spinner />
        ) : playing ? (
          <>
            <Bars playing className="h-3.5 text-pink group-hover:hidden" />
            <PauseIcon className="hidden h-4 w-4 group-hover:block" />
          </>
        ) : (
          <PlayIcon className="h-4 w-4" />
        )}
      </span>
    </button>
  );
}

/** Spotify page of a track (always available: every track has a Spotify URI). */
export function spotifyUrl(uri: string): string {
  return `https://open.spotify.com/track/${uri.split(":").pop()}`;
}
