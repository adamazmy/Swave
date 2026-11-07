// "I listen on": the music services a user can pick, as a provider list. Each entry says how it
// connects (Spotify: OAuth redirect; Apple Music later: MusicKit's authorize popup; Other: none),
// so a new service plugs into the same picker and save flow without UI changes.
// The choice is stored per user on the server (GET/PUT /me/service); one shared store keeps the
// header, the picker and the save bar in sync.

import { useEffect, useReducer } from "react";
import { api, type ServiceId, type ServiceState } from "./api";
import { writeProvider } from "./provider";

export type ConnectKind = "redirect" | "popup" | "none";

export type ServiceDef = {
  id: ServiceId;
  label: string;
  hint: string; // one line under the label
  available: boolean; // false = listed but not selectable yet ("coming soon")
  connect: ConnectKind;
  canSave: boolean; // saves a set straight to the service
};

export const SERVICES: ServiceDef[] = [
  { id: "spotify", label: "Spotify", hint: "Save sets straight to your Spotify", available: true,
    connect: "redirect", canSave: true },
  { id: "apple", label: "Apple Music", hint: "Coming soon", available: false,
    connect: "popup", canSave: false },
  { id: "other", label: "Other / just browsing", hint: "Copy the list, export a CSV or open songs one by one",
    available: true, connect: "none", canSave: false },
];

export const serviceDef = (id: ServiceId | null) => SERVICES.find((s) => s.id === id) ?? null;

/** Where "Save the set" goes: straight to Spotify, or the fallback bar (copy / CSV / links). */
export function saveMode(state: ServiceState | null): "spotify" | "fallback" {
  if (!state || state.service !== "spotify") return "fallback";
  return state.spotify.configured && !state.spotify.blocked ? "spotify" : "fallback";
}

/** The one friendly line for an account Spotify refuses (not on the app's allowlist). */
export function notAllowlistedLine(owner: string): string {
  return `Ask ${owner} to add your Spotify account; until then use Copy for Spotify.`;
}

// ── Resume after the OAuth round trip ──────────────────────────────────────────────────────────
// The page's state (seeds, results, kept songs, draft) is already in localStorage. Before the
// redirect we stash the interrupted action under a random id that rides in the return URL
// (`resume=<id>`, which the API keeps against the OAuth `state`); after the callback the page
// finishes that action. Stale (> 15 min) or mismatched ids are ignored.

export type ResumeAction = "save" | "save-new" | "add";
const RESUME_KEY = "swave-resume";
const RESUME_TTL_MS = 15 * 60 * 1000;
const RETURN_PARAMS = ["spotify", "spotify_error", "resume"];

function newId(): string {
  try {
    return crypto.randomUUID();
  } catch {
    return Math.random().toString(36).slice(2) + Date.now().toString(36);
  }
}

/** Remember `action` and build the URL to come back to (this page, plus the resume id). */
export function prepareReturn(href: string, action: ResumeAction | null, now = Date.now()): string {
  const url = new URL(href);
  RETURN_PARAMS.forEach((p) => url.searchParams.delete(p));
  const id = newId();
  try {
    sessionStorage.setItem(RESUME_KEY, JSON.stringify({ id, action, at: now }));
  } catch {
    /* storage blocked: we still come back, just without resuming */
  }
  url.searchParams.set("resume", id);
  return url.toString();
}

/** The action to finish after the callback (once), or null. */
export function takeResume(params: URLSearchParams, now = Date.now()): ResumeAction | null {
  const id = params.get("resume");
  if (!id) return null;
  try {
    const raw = sessionStorage.getItem(RESUME_KEY);
    sessionStorage.removeItem(RESUME_KEY);
    const stash = raw ? (JSON.parse(raw) as { id: string; action: ResumeAction | null; at: number }) : null;
    if (!stash || stash.id !== id || now - stash.at > RESUME_TTL_MS) return null;
    return stash.action;
  } catch {
    return null;
  }
}

/** The current URL without the OAuth return parameters (for router.replace after handling them). */
export function withoutReturnParams(href: string): string {
  const url = new URL(href);
  RETURN_PARAMS.forEach((p) => url.searchParams.delete(p));
  return url.pathname + url.search + url.hash;
}

/** Connect a service. Spotify: full-page OAuth redirect (PKCE) back to this page. */
export function connect(id: ServiceId, action: ResumeAction | null = null): void {
  const def = serviceDef(id);
  if (!def || !def.available) return;
  if (def.connect === "redirect" && id === "spotify") {
    window.location.href = api.loginUrl(prepareReturn(window.location.href, action));
  }
  // "popup" (Apple Music via MusicKit) plugs in here later; "none" needs no connection.
}

// ── Shared store ───────────────────────────────────────────────────────────────────────────────

let current: ServiceState | null = null;
let inflight: Promise<void> | null = null;
const listeners = new Set<() => void>();

function publish(s: ServiceState | null) {
  current = s;
  // Track links ("Open") follow the service: Spotify for Spotify and "Other" (tracks are Spotify ids).
  if (s?.service) writeProvider(s.service === "apple" ? "apple" : "spotify");
  listeners.forEach((f) => f());
}

export function refreshService(): Promise<void> {
  inflight ??= api
    .service()
    .then(publish)
    .catch(() => publish(null))
    .finally(() => {
      inflight = null;
    });
  return inflight;
}

export function useMusicService() {
  const [, rerender] = useReducer((x: number) => x + 1, 0);
  useEffect(() => {
    listeners.add(rerender);
    if (!current) void refreshService();
    return () => {
      listeners.delete(rerender);
    };
  }, []);

  /** Pick a service. Spotify not connected yet → straight into its OAuth redirect. Picking the
   *  current, already-connected service does nothing. */
  async function choose(id: ServiceId) {
    const def = serviceDef(id);
    if (!def?.available || id === "apple") return;
    const s = current;
    const connected = s?.spotify.connected ?? false;
    if (s?.service === id && (id !== "spotify" || connected)) return;
    publish(await api.setService(id));
    if (id === "spotify" && !connected) connect("spotify");
  }

  async function disconnect() {
    await api.logout();
    await refreshService();
  }

  return { state: current, choose, disconnect, refresh: refreshService };
}
