// Results-screen state kept for the browser tab, so a reload or the Spotify login redirect
// doesn't lose kept/removed songs. sessionStorage only: nothing leaves this browser.

import type { Recommendation, Track } from "./api";
import { sameSet } from "./playlist";

const KEY = "swave-results";

export type StoredResults = {
  seeds: string[];
  slider: number;
  list: Recommendation[];
  kept: string[];
  removed: string[];
  seedTracks: Track[];
  name: string;
};

/** Saved state for these seeds, or null (different seeds, nothing saved, storage blocked). */
export function readResults(seeds: string[]): StoredResults | null {
  try {
    const s = JSON.parse(sessionStorage.getItem(KEY) ?? "null") as StoredResults | null;
    if (s && Array.isArray(s.list) && s.list.length > 0 && sameSet(s.seeds, seeds)) return s;
  } catch {
    /* corrupt or blocked: start fresh */
  }
  return null;
}

export function writeResults(s: StoredResults): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(s));
  } catch {
    /* quota or blocked: not critical */
  }
}

export function clearResults(): void {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}
