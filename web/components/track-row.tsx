import type { Recommendation } from "@/lib/api";
import { shortReason } from "@/lib/playlist";
import { Bars, PauseIcon, PlayIcon, PlusIcon, Spinner, XIcon } from "./icons";

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
};

const focus = "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-pink";
const chip = `btn-label rounded-md border px-2.5 py-2 transition disabled:cursor-not-allowed disabled:opacity-30 ${focus}`;

export default function TrackRow(p: Props) {
  const { track: t } = p;
  return (
    <li
      className={`flex items-center gap-3 border-b border-line/70 px-2 py-2.5 transition sm:px-3 ${
        p.kept ? "bg-wine/45" : "hover:bg-raised/60"
      } ${p.pending ? "opacity-40" : ""}`}
    >
      <span className="label hidden w-6 shrink-0 text-pink/70 tabular-nums sm:block">
        {String(p.position + 1).padStart(2, "0")}
      </span>

      <PreviewButton name={t.name} state={p.preview} disabled={p.pending} onClick={p.onPreview} />

      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-semibold">{t.name}</p>
        <p className="truncate text-xs text-muted">
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
          <span className="hidden sm:inline">More like this</span>
          <PlusIcon className="h-3.5 w-3.5 sm:hidden" />
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
