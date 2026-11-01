// Pure list logic for the Results screen (unit-tested in playlist.test.ts).

export const LIST_SIZE = 40;
export const MAX_START_SEEDS = 5;
export const MAX_SEEDS = 10; // "more like this" can grow the seed set past the initial 5

const TRACK_URI = /^spotify:track:[A-Za-z0-9]{22}$/;

type HasUri = { track_uri: string };

/** Seeds from the `?seeds=` query param: valid, unique, at most MAX_SEEDS. */
export function parseSeeds(param: string | null): string[] {
  if (!param) return [];
  const seen = new Set<string>();
  for (const s of param.split(",")) {
    const uri = s.trim();
    if (TRACK_URI.test(uri)) seen.add(uri);
  }
  return [...seen].slice(0, MAX_SEEDS);
}

/** Slider 0..1 → popularity exponent alpha (0 = familiar, 1 = adventurous). */
export function sliderToAlpha(slider: number): number {
  return Math.min(1, Math.max(0, slider));
}

/** After a re-rank: kept tracks stay (in order) at the top, fresh results fill up to `size`. */
export function mergeKept<T extends HasUri>(kept: T[], fresh: T[], size = LIST_SIZE): T[] {
  const keptUris = new Set(kept.map((t) => t.track_uri));
  const rest = fresh.filter((t) => !keptUris.has(t.track_uri));
  return [...kept, ...rest.slice(0, Math.max(0, size - kept.length))];
}

/**
 * Kept tracks with up-to-date reasons after a re-rank: the fresh copy if the recommender still
 * returns it, "one of your seeds" if it became a seed (seeds are never recommended), else as-is.
 */
export function refreshKept<T extends HasUri & { reason: string }>(
  kept: T[],
  fresh: T[],
  seeds: string[],
): T[] {
  const byUri = new Map(fresh.map((t) => [t.track_uri, t]));
  return kept.map((k) => {
    const f = byUri.get(k.track_uri);
    if (f) return f;
    if (seeds.includes(k.track_uri)) return { ...k, reason: "one of your seeds" };
    return k;
  });
}

/** Replace the row at `index` with `replacement`, or drop it if there is none. */
export function replaceAt<T>(list: T[], index: number, replacement?: T): T[] {
  const next = [...list];
  if (replacement) next.splice(index, 1, replacement);
  else next.splice(index, 1);
  return next;
}

/** Add a seed (no duplicates, capped). Returns the same array if nothing changed. */
export function addSeed(seeds: string[], uri: string, max = MAX_SEEDS): string[] {
  if (seeds.includes(uri) || seeds.length >= max) return seeds;
  return [...seeds, uri];
}

/** Same members regardless of order. */
export function sameSet(a: string[], b: string[]): boolean {
  const sa = new Set(a);
  return sa.size === new Set(b).size && b.every((x) => sa.has(x));
}

type CsvTrack = { name: string; artist: string; album: string; track_uri: string };

/** CSV for playlist-transfer tools (TuneMyMusic, Soundiiz…): fallback when Spotify save fails. */
export function toCsv(tracks: CsvTrack[]): string {
  const cell = (s: string) => (/[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s);
  const rows = tracks.map((t) => [t.name, t.artist, t.album, t.track_uri].map(cell).join(","));
  return ["Track name,Artist name,Album,Spotify URI", ...rows].join("\r\n") + "\r\n";
}

export function slug(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60) || "playlist";
}

export function formatCount(n: number): string {
  return new Intl.NumberFormat("en", { notation: "compact" }).format(n);
}
