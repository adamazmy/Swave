// Which music service the user listens on: Spotify (save + import) or Apple Music (open links,
// export, pasted lists). The API says what each can do (`api.providers()`); the UI follows those
// capabilities, so a service that gains "write" later (MusicKit) gets the save flow.
// localStorage: this browser only.

import { useEffect, useState } from "react";
import { api, type ProviderInfo, type ProviderName } from "./api";

const KEY = "swave-provider";
const EVENT = "swave-provider";

// Used until /providers answers (and if the API is down).
const FALLBACK: ProviderInfo[] = [
  { name: "spotify", label: "Spotify", configured: true, can_search: true, can_read: true, can_write: true },
  { name: "apple", label: "Apple Music", configured: true, can_search: true, can_read: false, can_write: false },
];

export function readProvider(): ProviderName {
  try {
    return localStorage.getItem(KEY) === "apple" ? "apple" : "spotify";
  } catch {
    return "spotify";
  }
}

export function writeProvider(p: ProviderName): void {
  try {
    localStorage.setItem(KEY, p);
  } catch {
    /* storage blocked: lasts until reload */
  }
  window.dispatchEvent(new Event(EVENT));
}

let cached: Promise<ProviderInfo[]> | null = null;
function loadProviders(): Promise<ProviderInfo[]> {
  cached ??= api.providers().catch(() => {
    cached = null;
    return FALLBACK;
  });
  return cached;
}

/** The chosen service, its capabilities, and a setter. Every screen using it stays in sync. */
export function useProvider() {
  const [name, setName] = useState<ProviderName>(() => readProvider());
  const [all, setAll] = useState<ProviderInfo[]>(FALLBACK);
  useEffect(() => {
    let live = true;
    void loadProviders().then((p) => live && setAll(p));
    const sync = () => setName(readProvider());
    window.addEventListener(EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      live = false;
      window.removeEventListener(EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);
  const info = all.find((p) => p.name === name) ?? FALLBACK[0];
  return { name, info, all, set: (p: ProviderName) => writeProvider(p) };
}

/** Where a track opens for this service. */
export function openTrackUrl(provider: ProviderName, trackUri: string): string {
  return provider === "spotify"
    ? `https://open.spotify.com/track/${trackUri.split(":").pop()}`
    : api.openUrl(provider, trackUri);
}
