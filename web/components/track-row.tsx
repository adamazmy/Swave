import type { Recommendation } from "@/lib/api";
import { shortReason } from "@/lib/playlist";
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

      <PreviewSlot
        uri={t.track_uri}
        name={t.name}
        state={p.pending ? "loading" : p.preview}
        disabled={p.pending}
        onClick={p.onPreview}
        open={p.open}
      />

      <Cover src={p.cover} />

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
          {t.artist}
          {t.reason && <span className="text-dim" title={t.reason}> · {shortReason(t.reason)}</span>}
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

/** Album cover, or a blank tile while it loads / when there is none (keeps rows aligned). */
function Cover({ src }: { src?: string | null }) {
  return src ? (
    // eslint-disable-next-line @next/next/no-img-element
    <img src={src} alt="" width={40} height={40} loading="lazy" decoding="async"
         className="h-10 w-10 shrink-0 rounded-sm bg-raised object-cover" />
  ) : (
    <span aria-hidden className="h-10 w-10 shrink-0 rounded-sm bg-raised" />
  );
}

/** Spotify page of a track (always available: every track has a Spotify URI). */
export function spotifyUrl(uri: string): string {
  return `https://open.spotify.com/track/${uri.split(":").pop()}`;
}

/** The preview button, or, for a track with no preview anywhere, a link to open it on the
 *  user's service (`open`; Spotify by default). */
export function PreviewSlot(p: {
  uri: string;
  name: string;
  state: PreviewState;
  disabled?: boolean;
  onClick: () => void;
  open?: { href: string; label: string };
}) {
  const open = p.open ?? { href: spotifyUrl(p.uri), label: "Spotify" };
  if (p.state !== "unavailable") return <PreviewButton name={p.name} state={p.state} disabled={p.disabled} onClick={p.onClick} />;
  return (
    <a
      href={open.href}
      target="_blank"
      rel="noreferrer"
      className={`grid h-9 w-9 shrink-0 place-items-center rounded-md border border-dashed border-line text-dim transition hover:border-pink/60 hover:text-pink ${focus}`}
      aria-label={`No preview for ${p.name}: open it in ${open.label}`}
      title={`No preview available: open in ${open.label}`}
    >
      <ExternalIcon className="h-3.5 w-3.5" />
    </a>
  );
}

/** Square preview button: level bars at rest (a play icon on hover), bouncing bars while playing. */
export function PreviewButton({
  name,
  state,
  disabled = false,
  onClick,
}: {
  name: string;
  state: PreviewState;
  disabled?: boolean;
  onClick: () => void;
}) {
  const playing = state === "playing";
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={state === "unavailable" || disabled}
      className={`group grid h-9 w-9 shrink-0 place-items-center rounded-md border transition ${
        playing ? "border-pink bg-pink/15 text-pink" : "border-line bg-panel text-pink-soft hover:border-pink/60"
      } disabled:cursor-not-allowed disabled:border-line/50 disabled:text-dim/60 ${focus}`}
      aria-label={playing ? `Pause ${name}` : `Play preview of ${name}`}
      title={state === "unavailable" ? "No preview available" : "30-second preview"}
    >
      {state === "loading" ? (
        <Spinner />
      ) : playing ? (
        <>
          <Bars playing className="h-3.5 group-hover:hidden" />
          <PauseIcon className="hidden h-3.5 w-3.5 group-hover:block" />
        </>
      ) : (
        <>
          <Bars className="h-3.5 group-hover:hidden group-disabled:block" />
          <PlayIcon className="hidden h-3.5 w-3.5 group-hover:block group-disabled:hidden" />
        </>
      )}
    </button>
  );
}
