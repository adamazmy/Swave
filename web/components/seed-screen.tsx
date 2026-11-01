"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { api, ApiError, type Track } from "@/lib/api";
import { formatCount, MAX_START_SEEDS } from "@/lib/playlist";
import { newSession } from "@/lib/session";
import { writeSource, type SourcePlaylist } from "@/lib/source";
import { clearResults } from "@/lib/stored";
import { PlusIcon, Spinner, XIcon } from "./icons";

export default function SeedScreen() {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Track[]>([]);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [seeds, setSeeds] = useState<Track[]>([]);

  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const inflight = useRef<AbortController | null>(null);
  useEffect(() => () => {
    clearTimeout(timer.current);
    inflight.current?.abort();
  }, []);

  // Debounced search; aborts stale requests as you type.
  function onQuery(value: string) {
    setQuery(value);
    clearTimeout(timer.current);
    inflight.current?.abort();
    const q = value.trim();
    if (q.length < 2) {
      setResults([]);
      setSearching(false);
      return;
    }
    const ctrl = new AbortController();
    inflight.current = ctrl;
    setSearching(true);
    timer.current = setTimeout(async () => {
      try {
        setResults(await api.search(q, ctrl.signal));
        setError(null);
      } catch (err) {
        if ((err as Error).name !== "AbortError") {
          setError(err instanceof ApiError ? err.message : "Search failed");
        }
      } finally {
        if (!ctrl.signal.aborted) setSearching(false);
      }
    }, 250);
  }

  const full = seeds.length >= MAX_START_SEEDS;
  const isSeed = (t: Track) => seeds.some((s) => s.track_uri === t.track_uri);

  function add(t: Track) {
    if (full || isSeed(t)) return;
    setSeeds([...seeds, t]);
  }

  const [link, setLink] = useState("");
  const [importing, setImporting] = useState(false);
  const [importMsg, setImportMsg] = useState<{ text: string; connect?: boolean } | null>(null);
  // The pasted playlist and its songs that are in the dataset: you pick seeds from them.
  const [source, setSource] = useState<SourcePlaylist | null>(null);
  const [sourceTracks, setSourceTracks] = useState<Track[]>([]);

  async function importLink(e: React.FormEvent) {
    e.preventDefault();
    if (!link.trim()) return;
    setImporting(true);
    setImportMsg(null);
    try {
      const res = await api.importPlaylist(link.trim());
      setSource(res.playlist);
      setSourceTracks(res.tracks);
      setImportMsg({
        text:
          res.tracks.length === 0
            ? `None of the ${res.total} songs in “${res.playlist.name}” are in the dataset. Pick seeds by search instead.`
            : `${res.tracks.length} of ${res.total} songs in “${res.playlist.name}” are in the dataset. Pick up to ${MAX_START_SEEDS} as seeds.`,
      });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setImportMsg({ text: "Connect Spotify to read your playlists.", connect: true });
      } else {
        setImportMsg({ text: err instanceof ApiError ? err.message : "Couldn't read that playlist" });
      }
    } finally {
      setImporting(false);
    }
  }

  function start() {
    clearResults();
    const session = newSession();
    const run = session.slice(0, 8);
    // Results offers "Add to <playlist>" when the vibe came from a pasted playlist.
    writeSource(source, run);
    api.log(seeds.map((s, i) => ({ action: "seeded", track_uri: s.track_uri, position: i, session_id: session })));
    // A fresh `run` per vibe makes the (kept-alive) Results page start over; see results-client.
    router.push(`/results?seeds=${seeds.map((s) => s.track_uri).join(",")}&run=${run}`);
  }

  return (
    <div className="space-y-8">
      <header className="space-y-2">
        <h1 className="text-3xl font-semibold tracking-tight">Swave</h1>
        <p className="text-zinc-400">Pick 1–{MAX_START_SEEDS} songs that capture the vibe.</p>
      </header>

      {seeds.length > 0 && (
        <section aria-label="Your seeds" className="space-y-4">
          <ul className="flex flex-wrap gap-2">
            {seeds.map((s) => (
              <li key={s.track_uri} className="flex items-center gap-2 rounded-full bg-emerald-500/15 py-1 pl-3 pr-1 text-sm text-emerald-200">
                <span className="max-w-56 truncate">
                  {s.name} <span className="text-emerald-300/60">· {s.artist}</span>
                </span>
                <button
                  onClick={() => setSeeds(seeds.filter((x) => x.track_uri !== s.track_uri))}
                  className="rounded-full p-1 hover:bg-emerald-500/20"
                  aria-label={`Remove ${s.name}`}
                >
                  <XIcon className="h-3.5 w-3.5" />
                </button>
              </li>
            ))}
          </ul>
          <button
            onClick={start}
            className="rounded-lg bg-emerald-500 px-5 py-2.5 font-medium text-zinc-950 hover:bg-emerald-400 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-400"
          >
            Find songs →
          </button>
        </section>
      )}

      <section className="space-y-3">
        <label htmlFor="search" className="sr-only">Search songs</label>
        <div className="relative">
          <input
            id="search"
            type="search"
            autoFocus
            autoComplete="off"
            value={query}
            onChange={(e) => onQuery(e.target.value)}
            placeholder={full ? "That's 5 seeds. Find songs, or remove one" : "Search a song or artist…"}
            disabled={full}
            className="w-full rounded-lg border border-zinc-800 bg-zinc-900 px-4 py-3 text-base outline-none placeholder:text-zinc-500 focus:border-emerald-500/60 disabled:opacity-50"
          />
          {searching && <Spinner className="absolute right-3 top-3.5 h-5 w-5 text-zinc-500" />}
        </div>

        {error && <p role="alert" className="text-sm text-red-400">{error}</p>}

        {!full && results.length > 0 && <PickList tracks={results} onPick={add} isSeed={isSeed} />}

        {!searching && !error && query.trim().length >= 2 && results.length === 0 && (
          <p className="text-sm text-zinc-500">No songs found. Only tracks in the playlist dataset are searchable.</p>
        )}
      </section>

      <section className="space-y-2 border-t border-zinc-800/60 pt-6">
        <form onSubmit={(e) => void importLink(e)} className="flex flex-wrap gap-2">
          <label htmlFor="playlist-link" className="sr-only">Spotify playlist link</label>
          <input
            id="playlist-link"
            value={link}
            onChange={(e) => setLink(e.target.value)}
            placeholder="…or paste one of your Spotify playlist links"
            className="min-w-0 flex-1 basis-64 rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm outline-none placeholder:text-zinc-500 focus:border-emerald-500/60"
          />
          <button
            type="submit"
            disabled={importing || !link.trim()}
            className="rounded-lg border border-zinc-700 px-4 py-2 text-sm hover:bg-white/5 disabled:opacity-40"
          >
            {importing ? <Spinner className="inline h-4 w-4" /> : "Use playlist"}
          </button>
        </form>
        {importMsg && (
          <p className="text-sm text-zinc-400" aria-live="polite">
            {importMsg.text}{" "}
            {importMsg.connect && (
              <a href={api.loginUrl(typeof window === "undefined" ? "" : window.location.href)} className="text-emerald-400 underline">
                Connect Spotify
              </a>
            )}
          </p>
        )}
        {source && sourceTracks.length > 0 && (
          <div className="space-y-2">
            <div className="flex items-center justify-between text-sm">
              <span className="text-zinc-300">From “{source.name}”</span>
              <button
                onClick={() => {
                  setSource(null);
                  setSourceTracks([]);
                  setImportMsg(null);
                }}
                className="text-zinc-500 hover:text-zinc-300"
              >
                Clear
              </button>
            </div>
            <PickList tracks={sourceTracks} onPick={add} isSeed={isSeed} disabled={full} scroll />
          </div>
        )}
      </section>
    </div>
  );
}

/** Clickable song rows: pick one as a seed. Used for search results and pasted playlists. */
function PickList({
  tracks,
  onPick,
  isSeed,
  disabled = false,
  scroll = false,
}: {
  tracks: Track[];
  onPick: (t: Track) => void;
  isSeed: (t: Track) => boolean;
  disabled?: boolean;
  scroll?: boolean;
}) {
  return (
    <ul className={`divide-y divide-zinc-800/60 rounded-lg border border-zinc-800 ${scroll ? "max-h-80 overflow-y-auto" : ""}`}>
      {tracks.map((t) => (
        <li key={t.track_uri}>
          <button
            onClick={() => onPick(t)}
            disabled={disabled || isSeed(t)}
            aria-pressed={isSeed(t)}
            className="flex w-full items-center gap-3 px-4 py-2.5 text-left hover:bg-white/5 disabled:opacity-40"
          >
            <div className="min-w-0 flex-1">
              <p className="truncate">{t.name}</p>
              <p className="truncate text-sm text-zinc-400">{t.artist}</p>
            </div>
            <span className="shrink-0 text-xs text-zinc-500 tabular-nums">
              {formatCount(t.playlist_count)} playlists
            </span>
            <PlusIcon className="h-4 w-4 shrink-0 text-zinc-400" />
          </button>
        </li>
      ))}
    </ul>
  );
}
