// Typed client for the Swave FastAPI backend.

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export type Track = {
  track_uri: string;
  name: string;
  artist: string;
  album: string;
  playlist_count: number;
};

export type Recommendation = Track & {
  score: number;
  seed_hits: number;
  co_count: number;
  reason: string;
};

export type EventAction =
  | "shown"
  | "seeded"
  | "previewed"
  | "kept"
  | "removed"
  | "more_like_this"
  | "saved";

export type AppEvent = {
  action: EventAction;
  track_uri?: string;
  position?: number;
  slider?: number;
  session_id?: string;
};

export type AuthStatus = {
  configured: boolean;
  connected: boolean;
  display_name: string | null;
  can_upload_cover: boolean;
};

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export type ImportResult = {
  playlist: { id: string; name: string; provider: "spotify" | "paste" };
  tracks: Track[];
  total: number;
  missing: number;
  loose: number; // matched by title + artist (Spotify id changed since 2018)
  unmatched: string[];
};

export type ProviderName = "spotify" | "apple";
export type ProviderInfo = {
  name: ProviderName;
  label: string;
  configured: boolean;
  can_search: boolean; // open a track there
  can_read: boolean; // import its playlists from a link
  can_write: boolean; // save the set to it
};

export type PreviewSource = "deezer" | "itunes";
export type PreviewOutcome =
  | "ok"
  | "no_match"
  | "matched_no_preview"
  | "expired_url"
  | "playback_error"
  | "source_error";
export type PreviewResult = {
  track_uri: string;
  preview_url: string | null;
  source: PreviewSource | null;
  outcome: Exclude<PreviewOutcome, "expired_url" | "playback_error">;
  open_url: string | null; // the track at `source` (Apple Music link for iTunes previews)
};
export type MyData = {
  user: string; // your Cloudflare Access email ("local" when running on your own machine)
  events: number;
  first_event: string | null;
  last_event: string | null;
  preview_attempts: number;
  spotify_connected: boolean;
  spotify_name: string | null;
};
export type PreviewAvailability = {
  status: "yes" | "no" | "unknown";
  source: PreviewSource | null;
  open_url: string | null;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch (err) {
    if ((err as Error).name === "AbortError") throw err;
    throw new ApiError(0, "Can't reach the Swave API. Is `make api` running?");
  }
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* keep the generic message */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  search: (q: string, signal?: AbortSignal) =>
    request<Track[]>(`/search?${new URLSearchParams({ q, limit: "15" })}`, { signal }),

  /** `adventure` (slider 0..1) lets the API pick alpha per seed set; pass `alpha` to reuse one. */
  recommend: (body: {
    seeds: string[];
    adventure?: number;
    alpha?: number;
    exclude?: string[];
    exclude_songs?: string[]; // hidden in every version (same artist + title)
    new_artists?: boolean; // leave out the seeds' artists (and those of exclude_songs)
    max_per_artist?: number; // results per primary artist (server default 5; 0 = no cap)
    listed?: string[]; // tracks already on the list: they count toward max_per_artist
    n?: number;
  }) =>
    request<{ results: Recommendation[]; seeds: Track[]; unknown_seeds: string[]; alpha: number }>("/recommend", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  /** A fresh preview URL (they expire, so never reuse one). `exclude`: sources that failed to play. */
  preview: (trackUri: string, exclude: PreviewSource[] = []) =>
    request<PreviewResult>(
      `/preview?${new URLSearchParams([["track_uri", trackUri], ...exclude.map((s) => ["exclude", s])])}`,
    ),

  /** Album cover URL per track (null = none found). Cached server-side; up to 100 per call. */
  covers: (trackUris: string[]) =>
    request<Record<string, string | null>>("/covers", {
      method: "POST",
      body: JSON.stringify({ track_uris: trackUris }),
    }),

  /** Pre-resolve previews (cached server-side) and say which tracks have one. */
  resolvePreviews: (trackUris: string[]) =>
    request<Record<string, PreviewAvailability>>("/previews/resolve", {
      method: "POST",
      body: JSON.stringify({ track_uris: trackUris }),
    }),

  /** One row per play click, with its final outcome (preview coverage report). Never throws. */
  logPreviewAttempt: (a: {
    track_uri: string;
    outcome: PreviewOutcome;
    source: PreviewSource | null;
    steps: string | null;
    session_id: string;
  }) =>
    request<unknown>("/preview/attempts", { method: "POST", body: JSON.stringify(a) }).catch(() => undefined),

  authStatus: () => request<AuthStatus>("/auth/status"),

  /** Full-page redirect target: API → Spotify login → back to `returnTo`. */
  loginUrl: (returnTo: string) => `${API_URL}/auth/login?${new URLSearchParams({ return_to: returnTo })}`,

  logout: () => request<void>("/auth/logout", { method: "POST" }).catch(() => undefined),

  save: (body: {
    name: string;
    track_uris: string[];
    description?: string;
    playlist_id?: string;
    public?: boolean;
    cover_jpeg?: string; // base64 JPEG, no data: prefix
  }) =>
    request<{ id: string; url: string | null; added: number; cover_error: string | null }>("/save", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  /** Append songs to one of your playlists; songs already in it are skipped. */
  addToPlaylist: (playlistId: string, trackUris: string[]) =>
    request<{ id: string; url: string; added: number; skipped: number }>(
      `/playlists/${encodeURIComponent(playlistId)}/add`,
      { method: "POST", body: JSON.stringify({ track_uris: trackUris }) },
    ),

  importPlaylist: (url: string) =>
    request<ImportResult>("/import", { method: "POST", body: JSON.stringify({ url }) }),

  /** Seeds from a pasted song list (one per line; Apple Music copy-paste works). */
  importText: (text: string) =>
    request<ImportResult>("/import/text", { method: "POST", body: JSON.stringify({ text }) }),

  /** Music services and what Swave can do with each. */
  providers: () => request<ProviderInfo[]>("/providers"),

  /** Who you are and what Swave keeps about you (the "What Swave stores" page). */
  me: () => request<MyData>("/me"),
  /** Delete your events, preview attempts and Spotify login on the server. */
  deleteMyData: () =>
    request<{ deleted: Record<string, number> }>("/me/data", { method: "DELETE" }),

  /** Link that redirects to the track on a service (or a search there). */
  openUrl: (provider: string, trackUri: string) =>
    `${API_URL}/providers/${provider}/open?${new URLSearchParams({ track_uri: trackUri })}`,

  /** Fire-and-forget: logging must never break the UI. */
  log: (events: AppEvent[]) => {
    if (events.length === 0) return;
    void request("/events/batch", { method: "POST", body: JSON.stringify(events) }).catch(
      () => {},
    );
  },
};
