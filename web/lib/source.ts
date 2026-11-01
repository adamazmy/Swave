// The Spotify playlist a vibe was imported from, so Results can offer "Add to <playlist>" and
// "Hide songs already in <playlist>". Tied to the vibe's `run` id so another vibe never sees
// it. localStorage: survives refresh.

export type SourcePlaylist = {
  id: string;
  name: string;
  trackUris?: string[]; // its songs as dataset tracks (for "hide songs already in it")
};

const KEY = "swave-source";

export function writeSource(source: SourcePlaylist | null, run: string): void {
  try {
    if (source) localStorage.setItem(KEY, JSON.stringify({ ...source, run }));
    else localStorage.removeItem(KEY);
  } catch {
    /* storage blocked: the options just won't show */
  }
}

/** Source playlist of this run, or null. */
export function readSource(run: string | null): SourcePlaylist | null {
  try {
    const s = JSON.parse(localStorage.getItem(KEY) ?? "null");
    if (s && s.run === run && typeof s.id === "string" && typeof s.name === "string") {
      return { id: s.id, name: s.name, trackUris: Array.isArray(s.trackUris) ? s.trackUris : [] };
    }
  } catch {
    /* ignore */
  }
  return null;
}
