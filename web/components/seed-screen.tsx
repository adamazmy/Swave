"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { api, ApiError, type Track } from "@/lib/api";
import { formatCount, MAX_START_SEEDS } from "@/lib/playlist";
import { newSession } from "@/lib/session";
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

  async function importLink(e: React.FormEvent) {
    e.preventDefault();
    if (!link.trim()) return;
    setImporting(true);
    setImportMsg(null);
    try {
      const res = await api.importPlaylist(link.trim());
      if (res.tracks.length === 0) {
        setImportMsg({ text: `None of the playlist's ${res.total} songs are in the dataset. Pick seeds by search instead.` });
        return;
      }
      const picked = res.tracks.slice(0, MAX_START_SEEDS);
      setSeeds(picked);
      setImportMsg({
        text: `Found ${res.tracks.length} of ${res.total} songs in the dataset. Using the first ${picked.length} as seeds; edit them above.`,
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
    api.log(seeds.map((s, i) => ({ action: "seeded", track_uri: s.track_uri, position: i, session_id: session })));
    // A fresh `run` per vibe makes the (kept-alive) Results page start over; see results-client.
    router.push(`/results?seeds=${seeds.map((s) => s.track_uri).join(",")}&run=${session.slice(0, 8)}`);
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

        {!full && results.length > 0 && (
          <ul className="divide-y divide-zinc-800/60 rounded-lg border border-zinc-800">
            {results.map((t) => (
              <li key={t.track_uri}>
                <button
                  onClick={() => add(t)}
                  disabled={isSeed(t)}
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
        )}

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
      </section>
    </div>
  );
}
