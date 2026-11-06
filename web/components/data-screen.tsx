"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, ApiError, type MyData } from "@/lib/api";

/** Every key Swave writes to this browser's localStorage (all start with "swave-"). */
function swaveKeys(): string[] {
  try {
    return Object.keys(localStorage).filter((k) => k.startsWith("swave-"));
  } catch {
    return [];
  }
}

const date = (s: string | null) => (s ? new Date(s).toLocaleDateString() : "-");

export default function DataScreen() {
  const [me, setMe] = useState<MyData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [keys, setKeys] = useState<string[]>(swaveKeys); // browser-only component (data-client)

  useEffect(() => {
    api.me().then(setMe, (e) => setError(e instanceof ApiError ? e.message : "Couldn't load your data"));
  }, []);

  async function deleteAll() {
    setBusy(true);
    setError(null);
    try {
      const { deleted } = await api.deleteMyData();
      try {
        swaveKeys().forEach((k) => localStorage.removeItem(k));
        sessionStorage.clear();
      } catch {
        /* storage blocked: nothing stored there either */
      }
      setKeys([]);
      setDone(`Deleted ${deleted.events} listening events, ${deleted.preview_attempts} preview attempts`
        + `${deleted.spotify_login ? " and your Spotify login" : ""}, and cleared this browser.`);
      setMe(await api.me());
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Delete failed, nothing was removed");
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  }

  return (
    <section className="max-w-2xl space-y-8">
      <div>
        <h1 className="font-display text-2xl font-semibold tracking-wide">What Swave stores</h1>
        <p className="mt-2 text-sm text-muted">
          Swave is a small private app. This is everything it keeps about you, and how to remove it.
        </p>
      </div>

      <div className="space-y-3 rounded-md border border-line bg-panel/60 p-5 text-sm">
        <h2 className="label text-pink">On the server, linked to you</h2>
        {me ? (
          <ul className="space-y-2">
            <li><span className="text-muted">Signed in as</span> {me.user}</li>
            <li>
              <span className="text-muted">Listening events</span> {me.events.toLocaleString()}
              <span className="text-dim"> (songs shown, previewed, kept, removed, saved; {date(me.first_event)} to {date(me.last_event)})</span>
            </li>
            <li>
              <span className="text-muted">Preview attempts</span> {me.preview_attempts.toLocaleString()}
              <span className="text-dim"> (whether a 30-second preview played, to fix broken ones)</span>
            </li>
            <li>
              <span className="text-muted">Spotify login</span>{" "}
              {me.spotify_connected ? `connected as ${me.spotify_name}` : "not connected"}
              <span className="text-dim"> (a token to save playlists to your account; never shown or shared)</span>
            </li>
          </ul>
        ) : (
          <p className="text-muted">{error ?? "Loading…"}</p>
        )}
      </div>

      <div className="space-y-2 rounded-md border border-line bg-panel/60 p-5 text-sm">
        <h2 className="label text-pink">In this browser only</h2>
        <p className="text-muted">
          Your current set, seed draft, chosen music service and theme, kept in this browser&apos;s storage
          {keys.length ? ` (${keys.join(", ")})` : " (nothing stored right now)"}.
        </p>
      </div>

      <div className="space-y-2 rounded-md border border-line bg-panel/60 p-5 text-sm">
        <h2 className="label text-pink">Shared, not about you</h2>
        <p className="text-muted">
          Caches every user benefits from: album cover links, which preview matches which song, related
          artists, and the music index itself. None of it records who asked.
        </p>
      </div>

      <div className="space-y-3">
        {done && <p className="text-sm text-cream" role="status">{done}</p>}
        {error && me && <p className="text-sm text-pink" role="alert">{error}</p>}
        {!confirming ? (
          <button
            onClick={() => setConfirming(true)}
            disabled={!me}
            className="btn-label rounded-md border border-pink/70 px-4 py-2.5 text-pink hover:bg-pink/10 disabled:opacity-40"
          >
            Delete my data
          </button>
        ) : (
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-sm">Delete your events, preview attempts and Spotify login, and clear this browser?</span>
            <button
              onClick={() => void deleteAll()}
              disabled={busy}
              className="btn-label rounded-md border border-pink bg-pink px-4 py-2.5 text-ink hover:bg-pink/85 disabled:opacity-40"
            >
              {busy ? "Deleting…" : "Yes, delete"}
            </button>
            <button onClick={() => setConfirming(false)} disabled={busy}
                    className="btn-label rounded-md border border-line px-4 py-2.5 text-muted hover:text-text">
              Cancel
            </button>
          </div>
        )}
        <p className="text-xs text-dim">
          Playlists you already saved to Spotify stay in your Spotify account. <Link href="/" className="underline hover:text-text">Back to Swave</Link>
        </p>
      </div>
    </section>
  );
}
