// Everything a vibe needs to survive a refresh, a new tab or a browser restart, kept in
// localStorage (this browser only; nothing leaves it). "Start over" clears it all.

import type { Recommendation, Track } from "./api";
import { sameSet } from "./playlist";

const RESULTS = "swave-results";
const DRAFT = "swave-seed-draft";
const SOURCE = "swave-source";

export type SavedPlaylist = { id: string; url: string | null };

export type StoredResults = {
  run: string | null; // vibe id from the URL
  seeds: string[];
  slider: number;
  list: Recommendation[];
  kept: string[];
  removed: string[];
  seedTracks: Track[];
  name: string;
  description: string;
  cover: string | null; // base64 JPEG (no data: prefix), ≤ 256 KB
  alpha: number; // exponent the API picked for the current list (reused for replacements)
  hideSource?: boolean; // hide songs already in the pasted playlist (default on)
  saved: SavedPlaylist | null; // playlist this vibe was saved to: later saves update it
};

export type ImportInfo = { total: number; missing: number; loose: number; unmatched: string[] };

/** Seed screen in progress: picked seeds and the pasted playlist's songs. */
export type SeedDraft = {
  seeds: Track[];
  link: string;
  source: { id: string; name: string } | null;
  sourceTracks: Track[];
  importInfo: ImportInfo | null;
};

function read<T>(key: string): T | null {
  try {
    return JSON.parse(localStorage.getItem(key) ?? "null") as T | null;
  } catch {
    return null; // corrupt or storage blocked: start fresh
  }
}

function write(key: string, value: unknown): void {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* quota or blocked: not critical */
  }
}

/** Saved Results state for these seeds, or null. */
export function readResults(seeds: string[]): StoredResults | null {
  const s = read<StoredResults>(RESULTS);
  return s && Array.isArray(s.list) && s.list.length > 0 && sameSet(s.seeds, seeds) ? s : null;
}

/** The last vibe, whatever its seeds (for "Continue your last vibe"). */
export function readLatestResults(): StoredResults | null {
  const s = read<StoredResults>(RESULTS);
  return s && Array.isArray(s.list) && s.list.length > 0 && Array.isArray(s.seeds) ? s : null;
}

export function resultsUrl(s: Pick<StoredResults, "seeds" | "slider" | "run">): string {
  return `/results?seeds=${s.seeds.join(",")}&v=${s.slider}${s.run ? `&run=${s.run}` : ""}`;
}

export const writeResults = (s: StoredResults) => write(RESULTS, s);
export const clearResults = () => write(RESULTS, null);

export const readDraft = () => read<SeedDraft>(DRAFT);
export const writeDraft = (d: SeedDraft) => write(DRAFT, d);

/** "Start over": forget the vibe, the draft and the pasted playlist. */
export function clearAll(): void {
  write(RESULTS, null);
  write(DRAFT, null);
  write(SOURCE, null);
}
