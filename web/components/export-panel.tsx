"use client";

import { useState } from "react";
import type { ProviderInfo, Recommendation } from "@/lib/api";
import { slug, toCsv, toSongList } from "@/lib/playlist";
import { openTrackUrl } from "@/lib/provider";
import { ExternalIcon } from "./icons";

const btn =
  "btn-label rounded-md px-4 py-3 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-pink";
const primary = `${btn} bg-pink px-6 text-ink hover:bg-pink-soft`;
const secondary = `${btn} border border-line text-text hover:border-pink/50`;

/**
 * Sticky bottom bar for a service Swave can't save to (no "write" capability: Apple Music
 * without MusicKit). Take the set there by hand: copy the song list (transfer tools and Swave's
 * paste box read it), export a CSV, or open each kept song on the service.
 */
export default function ExportPanel({
  tracks,
  name,
  onName,
  provider,
}: {
  tracks: Recommendation[]; // kept songs, in list order
  name: string;
  onName: (name: string) => void;
  provider: ProviderInfo;
}) {
  const [copied, setCopied] = useState(false);
  const [showList, setShowList] = useState(false);
  const n = tracks.length;

  async function copy() {
    try {
      await navigator.clipboard.writeText(toSongList(tracks));
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    } catch {
      setShowList(true); // clipboard blocked: the list below is selectable
    }
  }

  function exportCsv() {
    const url = URL.createObjectURL(new Blob([toCsv(tracks)], { type: "text/csv;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${slug(name || "swave")}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="sticky bottom-0 -mx-4 max-h-[80vh] overflow-y-auto border-t border-line bg-ink/95 px-4 py-4 backdrop-blur sm:-mx-8 sm:px-8">
      {showList && n > 0 && (
        <ol className="mb-3 max-h-60 overflow-y-auto rounded-md border border-line bg-panel">
          {tracks.map((t) => (
            <li key={t.track_uri} className="flex items-center gap-3 border-b border-line/60 px-3 py-2 text-sm last:border-b-0">
              <span className="min-w-0 flex-1 truncate">
                {t.name} <span className="text-muted">- {t.artist}</span>
              </span>
              <a
                href={openTrackUrl(provider.name, t.track_uri)}
                target="_blank"
                rel="noreferrer"
                className="label flex shrink-0 items-center gap-1 text-pink hover:text-pink-soft"
              >
                Open <ExternalIcon className="h-3 w-3" />
              </a>
            </li>
          ))}
        </ol>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <span className="label mr-2 text-cream tabular-nums">{String(n).padStart(2, "0")} kept</span>
        <label htmlFor="export-name" className="sr-only">
          Set name
        </label>
        <input
          id="export-name"
          value={name}
          onChange={(e) => onName(e.target.value)}
          maxLength={100}
          placeholder="Set name"
          className="min-w-0 flex-1 basis-48 rounded-md border border-line bg-panel px-3 py-2.5 text-sm outline-none placeholder:text-dim focus:border-pink/60"
        />
        <button onClick={() => void copy()} disabled={n === 0} className={primary}>
          {copied ? "Copied" : provider.name === "spotify" ? "Copy for Spotify" : "Copy song list"}
        </button>
        <button onClick={() => setShowList((s) => !s)} disabled={n === 0} aria-expanded={showList} className={secondary}>
          {showList ? "Hide songs" : `Open in ${provider.label}`}
        </button>
        <button onClick={exportCsv} disabled={n === 0} className={secondary}>
          Export CSV
        </button>
      </div>

      <p className="mt-2 text-xs text-dim" aria-live="polite">
        {n === 0
          ? "Keep songs to build your set."
          : provider.name === "spotify"
            ? "Paste the copied list into a playlist transfer tool (TuneMyMusic, Soundiiz…) to make it a Spotify playlist, or open each song in Spotify and add it there."
            : `Swave can't add playlists to ${provider.label} directly. Paste the copied list into a playlist transfer tool (TuneMyMusic, SongShift…), or open each song and add it there.`}
      </p>
    </div>
  );
}
