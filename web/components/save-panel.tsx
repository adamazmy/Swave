"use client";

import { useEffect, useRef, useState } from "react";
import { api, ApiError, type AuthStatus, type Recommendation } from "@/lib/api";
import { coverFromFile, generateCover } from "@/lib/cover";
import { slug, toCsv } from "@/lib/playlist";
import type { SourcePlaylist } from "@/lib/source";
import type { SavedPlaylist } from "@/lib/stored";
import { Spinner } from "./icons";

// No public/private option: Spotify currently makes API-created playlists public whatever we
// send (verified 2026-11-01: public=false on create and on update both ignored).
export type PlaylistDetails = { name: string; description: string; cover: string | null };

type Props = {
  tracks: Recommendation[]; // kept songs, in list order
  details: PlaylistDetails;
  onDetails: (patch: Partial<PlaylistDetails>) => void;
  source: SourcePlaylist | null; // playlist the vibe was imported from
  saved: SavedPlaylist | null; // new playlist this vibe was already saved to
  onSaved: (playlist: SavedPlaylist | null) => void; // null = songs added to `source`
};

type Mode = "new" | "add";
type Result = { text: string; url: string | null; warning?: string };

const btn =
  "rounded-lg px-4 py-2 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-400";
const primary = `${btn} bg-emerald-500 text-zinc-950 hover:bg-emerald-400`;
const secondary = `${btn} border border-zinc-700 text-zinc-200 hover:bg-white/5`;
const small = "rounded-md border border-zinc-700 px-2.5 py-1 text-xs text-zinc-200 hover:bg-white/5 disabled:opacity-40";

/**
 * Sticky bottom bar.
 * - New playlist: name + optional customisation (cover, description, public). The first save
 *   creates it; later saves update the same playlist unless "Save as new playlist".
 * - Add to <source>: appends kept songs to the pasted playlist, skipping ones already in it.
 * - Export CSV: fallback for playlist-transfer tools.
 */
export default function SavePanel({ tracks, details, onDetails, source, saved, onSaved }: Props) {
  const [auth, setAuth] = useState<AuthStatus | null>(null);
  const [mode, setMode] = useState<Mode>("new");
  const [customise, setCustomise] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [coverBusy, setCoverBusy] = useState(false);
  const variant = useRef(0);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let live = true;
    api
      .authStatus()
      .then((a) => live && setAuth(a))
      .catch(
        () => live && setAuth({ configured: false, connected: false, display_name: null, can_upload_cover: false }),
      );
    return () => {
      live = false;
    };
  }, []);

  const n = tracks.length;
  const name = details.name.trim() || "Swave playlist";
  const uris = tracks.map((t) => t.track_uri);
  const coverBlocked = details.cover !== null && auth?.connected && !auth.can_upload_cover;

  function fail(err: unknown) {
    if (err instanceof ApiError && err.status === 401) {
      setAuth((a) => (a ? { ...a, connected: false, display_name: null } : a));
    }
    const msg = err instanceof ApiError ? err.message : "Something went wrong";
    setError(`${msg}. You can export a CSV instead.`);
  }

  async function saveNew(asNew: boolean) {
    if (busy) return;
    setBusy(true);
    setError(null);
    setResult(null);
    const update = !asNew && saved !== null;
    try {
      const res = await api.save({
        name,
        description: details.description.trim(),
        track_uris: uris,
        playlist_id: update ? saved.id : undefined,
        cover_jpeg: details.cover && auth?.can_upload_cover ? details.cover : undefined,
      });
      setResult({
        text: `${update ? "Updated" : "Saved"} “${name}” with ${res.added} songs.`,
        url: res.url,
        warning: res.cover_error ?? (coverBlocked ? "Cover not applied: reconnect Spotify to allow covers." : undefined),
      });
      onSaved({ id: res.id, url: res.url });
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  }

  async function addToSource() {
    if (busy || !source) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const res = await api.addToPlaylist(source.id, uris);
      const skipped = res.skipped ? ` (${res.skipped} already there)` : "";
      setResult({ text: `Added ${res.added} new songs to “${source.name}”${skipped}.`, url: res.url });
      onSaved(null);
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  }

  async function onFile(file: File | undefined) {
    if (!file) return;
    setCoverBusy(true);
    setError(null);
    try {
      onDetails({ cover: await coverFromFile(file) });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't read that image");
    } finally {
      setCoverBusy(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  function generate() {
    try {
      onDetails({ cover: generateCover(name, variant.current++) });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't generate a cover");
    }
  }

  function exportCsv() {
    const url = URL.createObjectURL(new Blob([toCsv(tracks)], { type: "text/csv;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${slug(name)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  function connect() {
    window.location.href = api.loginUrl(window.location.href);
  }

  const connected = auth?.connected;
  const link = (url: string | null) =>
    url && (
      <a href={url} target="_blank" rel="noreferrer" className="underline">
        Open in Spotify ↗
      </a>
    );

  return (
    <div className="sticky bottom-0 -mx-4 max-h-[80vh] overflow-y-auto border-t border-zinc-800 bg-[#0a0a0b]/95 px-4 py-3 backdrop-blur">
      {source && (
        <div role="tablist" aria-label="Save to" className="mb-3 inline-flex rounded-lg bg-zinc-900 p-1 text-sm">
          {(["new", "add"] as const).map((m) => (
            <button
              key={m}
              role="tab"
              aria-selected={mode === m}
              onClick={() => {
                setMode(m);
                setResult(null);
                setError(null);
              }}
              className={`max-w-64 truncate rounded-md px-3 py-1.5 ${mode === m ? "bg-zinc-700 text-zinc-50" : "text-zinc-400 hover:text-zinc-200"}`}
            >
              {m === "new" ? "New playlist" : `Add to “${source.name}”`}
            </button>
          ))}
        </div>
      )}

      {mode === "new" && customise && (
        <div className="mb-3 flex flex-wrap gap-4 rounded-lg border border-zinc-800 p-3">
          <div className="flex flex-col items-start gap-2">
            {details.cover ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={`data:image/jpeg;base64,${details.cover}`}
                alt="Playlist cover preview"
                className="h-28 w-28 rounded-md object-cover"
              />
            ) : (
              <div className="grid h-28 w-28 place-items-center rounded-md border border-dashed border-zinc-700 text-xs text-zinc-500">
                No cover
              </div>
            )}
            <div className="flex flex-wrap gap-1.5">
              <input
                ref={fileInput}
                type="file"
                accept="image/*"
                className="sr-only"
                id="cover-file"
                onChange={(e) => void onFile(e.target.files?.[0])}
              />
              <label htmlFor="cover-file" className={`${small} cursor-pointer`}>
                {coverBusy ? "…" : "Upload"}
              </label>
              <button onClick={generate} className={small}>
                Generate
              </button>
              {details.cover && (
                <button onClick={() => onDetails({ cover: null })} className={small}>
                  Remove
                </button>
              )}
            </div>
          </div>
          <div className="flex min-w-48 flex-1 flex-col gap-2">
            <label className="text-xs text-zinc-400" htmlFor="playlist-desc">
              Description
            </label>
            <textarea
              id="playlist-desc"
              value={details.description}
              onChange={(e) => onDetails({ description: e.target.value })}
              maxLength={300}
              rows={3}
              className="rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm outline-none focus:border-emerald-500/60"
            />
            <p className="text-xs text-zinc-500">
              Spotify makes playlists created by apps public. To hide it, open it in Spotify → ⋯ → Make private.
            </p>
            {coverBlocked && (
              <p className="text-xs text-amber-300">
                Covers need one more Spotify permission.{" "}
                <button onClick={connect} className="underline">
                  Reconnect Spotify
                </button>
              </p>
            )}
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {mode === "new" ? (
          <>
            <label htmlFor="playlist-name" className="sr-only">
              Playlist name
            </label>
            <input
              id="playlist-name"
              value={details.name}
              onChange={(e) => onDetails({ name: e.target.value })}
              maxLength={100}
              placeholder="Playlist name"
              className="min-w-0 flex-1 basis-48 rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm outline-none focus:border-emerald-500/60"
            />
            <button onClick={() => setCustomise((c) => !c)} aria-expanded={customise} className={secondary}>
              {customise ? "Hide options" : "Customise"}
            </button>
          </>
        ) : (
          <p className="min-w-0 flex-1 basis-48 text-sm text-zinc-400">
            Appends your kept songs to “{source?.name}”. Songs already in it are skipped; nothing is removed.
          </p>
        )}

        {connected ? (
          mode === "new" ? (
            <button onClick={() => void saveNew(false)} disabled={n === 0 || busy} className={primary}>
              {busy ? <Spinner className="inline h-4 w-4" /> : saved ? `Update playlist (${n})` : `Save ${n} to Spotify`}
            </button>
          ) : (
            <button onClick={() => void addToSource()} disabled={n === 0 || busy} className={primary}>
              {busy ? <Spinner className="inline h-4 w-4" /> : `Add ${n} songs`}
            </button>
          )
        ) : auth?.configured ? (
          <button onClick={connect} className={primary}>
            Connect Spotify
          </button>
        ) : null}
        <button onClick={exportCsv} disabled={n === 0} className={secondary}>
          Export CSV
        </button>
      </div>

      <p className="mt-1.5 text-xs text-zinc-500" aria-live="polite">
        {result ? (
          <>
            <span className="text-emerald-300">
              {result.text} {link(result.url)}
            </span>
            {result.warning && <span className="block text-amber-300">{result.warning}</span>}
          </>
        ) : error ? (
          <span className="text-red-300">{error}</span>
        ) : n === 0 ? (
          "Keep songs (✓) to build your playlist."
        ) : auth && !auth.configured ? (
          "Spotify isn't set up (SPOTIFY_CLIENT_ID missing in .env). Export a CSV instead."
        ) : mode === "new" && saved && connected ? (
          <>
            Saved to Spotify; updating replaces its songs with your {n} kept. {link(saved.url)}
          </>
        ) : connected ? (
          `${n} kept · saves a playlist as ${auth?.display_name}`
        ) : (
          `${n} kept`
        )}
        {mode === "new" && saved && connected && (
          <>
            {" · "}
            <button
              onClick={() => void saveNew(true)}
              disabled={n === 0 || busy}
              className="underline hover:text-zinc-300 disabled:opacity-40"
            >
              Save as new playlist
            </button>
          </>
        )}
      </p>
    </div>
  );
}
