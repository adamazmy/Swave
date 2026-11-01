"use client";

import { useEffect, useState } from "react";
import { api, ApiError, type AuthStatus, type Recommendation } from "@/lib/api";
import { slug, toCsv } from "@/lib/playlist";
import { Spinner } from "./icons";

type Props = {
  tracks: Recommendation[]; // kept songs, in list order
  name: string;
  onNameChange: (name: string) => void;
  onSaved: () => void;
};

const btn =
  "rounded-lg px-4 py-2 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-400";

/** Sticky bottom bar: name the playlist, save it to Spotify, or export a CSV fallback. */
export default function SavePanel({ tracks, name, onNameChange, onSaved }: Props) {
  const [auth, setAuth] = useState<AuthStatus | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<{ url: string | null; added: number } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api
      .authStatus()
      .then((a) => live && setAuth(a))
      .catch(() => live && setAuth({ configured: false, connected: false, display_name: null }));
    return () => {
      live = false;
    };
  }, []);

  const n = tracks.length;
  const playlistName = name.trim() || "Swave playlist";

  async function save() {
    setSaving(true);
    setError(null);
    setSaved(null);
    try {
      const res = await api.save({ name: playlistName, track_uris: tracks.map((t) => t.track_uri) });
      setSaved(res);
      onSaved();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setAuth((a) => (a ? { ...a, connected: false, display_name: null } : a));
      }
      const msg = err instanceof ApiError ? err.message : "Save failed";
      setError(`${msg}. You can export a CSV instead.`);
    } finally {
      setSaving(false);
    }
  }

  function exportCsv() {
    const url = URL.createObjectURL(new Blob([toCsv(tracks)], { type: "text/csv;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${slug(playlistName)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  function connect() {
    window.location.href = api.loginUrl(window.location.href);
  }

  return (
    <div className="sticky bottom-0 -mx-4 border-t border-zinc-800 bg-[#0a0a0b]/95 px-4 py-3 backdrop-blur">
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor="playlist-name" className="sr-only">Playlist name</label>
        <input
          id="playlist-name"
          value={name}
          onChange={(e) => onNameChange(e.target.value)}
          maxLength={100}
          placeholder="Playlist name"
          className="min-w-0 flex-1 basis-48 rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm outline-none focus:border-emerald-500/60"
        />
        {auth?.connected ? (
          <button onClick={() => void save()} disabled={n === 0 || saving} className={`${btn} bg-emerald-500 text-zinc-950 hover:bg-emerald-400`}>
            {saving ? <Spinner className="inline h-4 w-4" /> : `Save ${n} to Spotify`}
          </button>
        ) : auth?.configured ? (
          <button onClick={connect} className={`${btn} bg-emerald-500 text-zinc-950 hover:bg-emerald-400`}>
            Connect Spotify
          </button>
        ) : null}
        <button onClick={exportCsv} disabled={n === 0} className={`${btn} border border-zinc-700 text-zinc-200 hover:bg-white/5`}>
          Export CSV
        </button>
      </div>

      <p className="mt-1.5 text-xs text-zinc-500" aria-live="polite">
        {saved ? (
          <span className="text-emerald-300">
            Saved {saved.added} songs to Spotify.{" "}
            {saved.url && (
              <a href={saved.url} target="_blank" rel="noreferrer" className="underline">Open in Spotify ↗</a>
            )}
          </span>
        ) : error ? (
          <span className="text-red-300">{error}</span>
        ) : n === 0 ? (
          "Keep songs (✓) to build your playlist."
        ) : auth && !auth.configured ? (
          "Spotify isn't set up (SPOTIFY_CLIENT_ID missing in .env). Export a CSV instead."
        ) : auth?.connected ? (
          `${n} kept · saves a private playlist as ${auth.display_name}`
        ) : (
          `${n} kept`
        )}
      </p>
    </div>
  );
}
