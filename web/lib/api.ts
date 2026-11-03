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
    request<{
      playlist: { id: string; name: string };
      tracks: Track[];
      total: number;
      missing: number;
      loose: number; // matched by title + artist (Spotify id changed since 2018)
      unmatched: string[];
    }>("/import", {
      method: "POST",
      body: JSON.stringify({ url }),
    }),

  /** Fire-and-forget: logging must never break the UI. */
  log: (events: AppEvent[]) => {
    if (events.length === 0) return;
    void request("/events/batch", { method: "POST", body: JSON.stringify(events) }).catch(
      () => {},
    );
  },
};
