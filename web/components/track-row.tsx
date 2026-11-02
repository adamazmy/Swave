import type { Recommendation } from "@/lib/api";
import { CheckIcon, PauseIcon, PlayIcon, PlusIcon, Spinner, XIcon } from "./icons";

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

const iconBtn =
  "rounded-md p-2 text-zinc-400 hover:bg-white/10 hover:text-zinc-100 disabled:opacity-30 disabled:hover:bg-transparent focus-visible:outline-2 focus-visible:outline-emerald-400";

export default function TrackRow(p: Props) {
  const { track: t } = p;
  return (
    <li
      className={`flex items-center gap-3 rounded-lg border px-2 py-2 transition sm:px-3 ${
        p.kept ? "border-emerald-500/40 bg-emerald-500/5" : "border-transparent hover:bg-white/[0.03]"
      } ${p.pending ? "opacity-40" : ""}`}
    >
      <span className="hidden w-6 shrink-0 text-right text-xs text-zinc-600 tabular-nums sm:block">
        {p.position + 1}
      </span>

      <PreviewButton name={t.name} state={p.preview} disabled={p.pending} onClick={p.onPreview} />

      <div className="min-w-0 flex-1">
        <p className="truncate font-medium">{t.name}</p>
        <p className="truncate text-sm text-zinc-400">{t.artist}</p>
        <p className="truncate text-xs text-zinc-500">{t.reason}</p>
      </div>

      <div className="flex shrink-0 items-center">
        <button
          onClick={p.onKeep}
          disabled={p.pending}
          aria-pressed={p.kept}
          className={`${iconBtn} ${p.kept ? "text-emerald-400" : ""}`}
          aria-label={p.kept ? `Un-keep ${t.name}` : `Keep ${t.name}`}
          title={p.kept ? "Kept" : "Keep"}
        >
          <CheckIcon />
        </button>
        <button
          onClick={p.onMore}
          disabled={p.pending || !p.canAddSeed}
          className={iconBtn}
          aria-label={`More like ${t.name}`}
          title={p.canAddSeed ? "More like this (adds it as a seed)" : "Seed limit reached"}
        >
          <PlusIcon />
        </button>
        <button
          onClick={p.onRemove}
          disabled={p.pending}
          className={iconBtn}
          aria-label={`Remove ${t.name}`}
          title="Remove (replace with the next best)"
        >
          <XIcon />
        </button>
      </div>
    </li>
  );
}

/** Round play/pause button for a track's 30-second preview. */
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
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={state === "unavailable" || disabled}
      className={`grid h-9 w-9 shrink-0 place-items-center rounded-full ${
        state === "playing" ? "bg-emerald-500 text-zinc-950" : "bg-zinc-800 text-zinc-200 hover:bg-zinc-700"
      } disabled:bg-zinc-900 disabled:text-zinc-600`}
      aria-label={state === "playing" ? `Pause ${name}` : `Play preview of ${name}`}
      title={state === "unavailable" ? "No preview available" : "30-second preview"}
    >
      {state === "loading" ? <Spinner /> : state === "playing" ? <PauseIcon /> : <PlayIcon />}
    </button>
  );
}
