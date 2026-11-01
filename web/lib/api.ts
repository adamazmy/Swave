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

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

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
  return res.json() as Promise<T>;
}

export const api = {
  search: (q: string, signal?: AbortSignal) =>
    request<Track[]>(`/search?${new URLSearchParams({ q, limit: "15" })}`, { signal }),

  recommend: (body: { seeds: string[]; alpha: number; exclude?: string[]; n?: number }) =>
    request<{ results: Recommendation[]; seeds: Track[]; unknown_seeds: string[] }>("/recommend", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  preview: (trackUri: string) =>
    request<{ track_uri: string; preview_url: string | null }>(
      `/preview?${new URLSearchParams({ track_uri: trackUri })}`,
    ),

  /** Fire-and-forget: logging must never break the UI. */
  log: (events: AppEvent[]) => {
    if (events.length === 0) return;
    void request("/events/batch", { method: "POST", body: JSON.stringify(events) }).catch(
      () => {},
    );
  },
};
