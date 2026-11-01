// The Spotify playlist a vibe was imported from, so Results can offer "Add to <playlist>".
// Tied to the vibe's `run` id: another vibe in the same tab (e.g. via Back) must not see it.
// sessionStorage: per tab, nothing leaves the browser.

export type SourcePlaylist = { id: string; name: string };

const KEY = "swave-source";

export function writeSource(source: SourcePlaylist | null, run: string): void {
  try {
    if (source) sessionStorage.setItem(KEY, JSON.stringify({ ...source, run }));
    else sessionStorage.removeItem(KEY);
  } catch {
    /* storage blocked: the option just won't show */
  }
}

/** Source playlist of this run, or null. */
export function readSource(run: string | null): SourcePlaylist | null {
  try {
    const s = JSON.parse(sessionStorage.getItem(KEY) ?? "null");
    if (s && s.run === run && typeof s.id === "string" && typeof s.name === "string") {
      return { id: s.id, name: s.name };
    }
  } catch {
    /* ignore */
  }
  return null;
}
