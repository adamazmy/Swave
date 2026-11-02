"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { api, ApiError, type Track } from "@/lib/api";
import { formatCount, isPlaylistLink, MAX_START_SEEDS, sameSet } from "@/lib/playlist";
import { newSession } from "@/lib/session";
import { writeSource, type SourcePlaylist } from "@/lib/source";
import {
  clearAll,
  clearResults,
  readDraft,
  readLatestResults,
  resultsUrl,
  writeDraft,
  type ImportInfo,
} from "@/lib/stored";
import { usePreview } from "@/lib/use-preview";
import { PlusIcon, Spinner, XIcon } from "./icons";
import { Mosaic, Ticker } from "./night";
import { PreviewButton } from "./track-row";

export default function SeedScreen() {
  const router = useRouter();
  // Browser-only component (see seed-client.tsx): safe to read localStorage in initialisers.
  const [draft] = useState(() => readDraft());
  const [latest, setLatest] = useState(() => readLatestResults());
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Track[]>([]);
  const preview = usePreview();
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [seeds, setSeeds] = useState<Track[]>(() => draft?.seeds ?? []);

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

  const [link, setLink] = useState(() => draft?.link ?? "");
  const [importing, setImporting] = useState(false);
  // Transient problems only (errors, "connect first"); the import summary comes from importInfo.
  const [importMsg, setImportMsg] = useState<{ text: string; connect?: boolean } | null>(null);
  // The pasted playlist and its songs that are in the dataset: you pick seeds from them.
  const [source, setSource] = useState<SourcePlaylist | null>(() => draft?.source ?? null);
  const [sourceTracks, setSourceTracks] = useState<Track[]>(() => draft?.sourceTracks ?? []);
  const [importInfo, setImportInfo] = useState<ImportInfo | null>(() => draft?.importInfo ?? null);

  // Keep the draft across refreshes until "Start over".
  useEffect(() => {
    writeDraft({ seeds, link, source, sourceTracks, importInfo });
  }, [seeds, link, source, sourceTracks, importInfo]);

  function startOver() {
    clearAll();
    setSeeds([]);
    setLink("");
    setSource(null);
    setSourceTracks([]);
    setImportInfo(null);
    setImportMsg(null);
    setQuery("");
    setResults([]);
    setLatest(null);
  }

  // One box for both: a search, or a pasted playlist link (loaded on Enter).
  function onInput(value: string) {
    setImportMsg(null);
    if (isPlaylistLink(value)) {
      clearTimeout(timer.current);
      inflight.current?.abort();
      setQuery(value);
      setResults([]);
      setSearching(false);
      return;
    }
    onQuery(value);
  }

  async function importLink() {
    const url = query.trim();
    if (!isPlaylistLink(url)) return;
    setImporting(true);
    setImportMsg(null);
    try {
      const res = await api.importPlaylist(url);
      setLink(url);
      setQuery("");
      setSource(res.playlist);
      setSourceTracks(res.tracks);
      setImportInfo({ total: res.total, missing: res.missing, loose: res.loose, unmatched: res.unmatched });
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
    // Same seeds as the saved vibe (e.g. back from "Edit seeds"): continue it, keep its songs.
    const last = readLatestResults();
    if (last && sameSet(last.seeds, seeds.map((s) => s.track_uri))) {
      router.push(resultsUrl(last));
      return;
    }
    clearResults();
    const session = newSession();
    const run = session.slice(0, 8);
    // Results offers "Add to <playlist>" when the vibe came from a pasted playlist.
    writeSource(source && { ...source, trackUris: sourceTracks.map((t) => t.track_uri) }, run);
    api.log(seeds.map((s, i) => ({ action: "seeded", track_uri: s.track_uri, position: i, session_id: session })));
    // A fresh `run` per vibe makes the (kept-alive) Results page start over; see results-client.
    router.push(`/results?seeds=${seeds.map((s) => s.track_uri).join(",")}&run=${run}`);
  }

  const linkMode = isPlaylistLink(query);

  return (
    <div className="space-y-8">
      <section className="grid items-center gap-6 sm:grid-cols-[1.15fr_1fr]">
        <div className="space-y-5">
          <h1 className="font-display text-5xl leading-[0.95] sm:text-6xl">
            Make the
            <br />
            <span className="text-pink">night.</span>
          </h1>
          <p className="max-w-xs text-sm leading-relaxed text-muted">
            Feed it a few tracks. Swave finds what people play next to them and cuts you a set.
          </p>
        </div>
        <Mosaic className="mx-auto hidden w-full max-w-xs sm:grid" />
      </section>

      <Ticker
        items={seeds.map((s) => s.name)}
        empty="Now loaded ▸ nothing yet ▸ add up to five tracks"
      />

      {latest && (
        <section aria-label="Your last set" className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-line bg-panel px-4 py-3">
          <div className="min-w-0">
            <p className="label text-dim">Your last set</p>
            <p className="truncate text-sm">
              {latest.seedTracks.map((s) => s.name).join(", ") || `${latest.seeds.length} seeds`}
              <span className="text-muted"> · {latest.kept.length} kept</span>
            </p>
          </div>
          <Link
            href={resultsUrl(latest)}
            className="btn-label rounded-md border border-cream/50 px-4 py-2.5 text-cream hover:bg-cream/10"
          >
            Continue →
          </Link>
        </section>
      )}

      <section aria-label="Your seeds" className="space-y-4">
        {seeds.length > 0 && (
          <ul className="flex flex-wrap gap-2">
            {seeds.map((s, i) => (
              <li key={s.track_uri} className="flex items-center gap-2 rounded-md border border-line bg-panel py-1.5 pl-2.5 pr-1 text-xs">
                <span className="font-mono text-pink">{String(i + 1).padStart(2, "0")}</span>
                <span className="max-w-48 truncate font-semibold" title={`${s.name} · ${s.artist}`}>{s.name}</span>
                <button
                  onClick={() => setSeeds(seeds.filter((x) => x.track_uri !== s.track_uri))}
                  className="rounded p-1 text-muted hover:bg-white/5 hover:text-text"
                  aria-label={`Remove ${s.name}`}
                >
                  <XIcon className="h-3 w-3" />
                </button>
              </li>
            ))}
          </ul>
        )}

        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (linkMode) void importLink();
          }}
          className="flex flex-col gap-3 sm:flex-row"
        >
          <div className="relative min-w-0 flex-1">
            <label htmlFor="search" className="sr-only">Add a track, or paste a playlist link</label>
            <input
              id="search"
              type="search"
              autoFocus
              autoComplete="off"
              value={query}
              onChange={(e) => onInput(e.target.value)}
              placeholder={full ? "That's five. Build the set, or remove one" : "Add a track, or paste a playlist link"}
              className="w-full rounded-md border border-line bg-panel px-4 py-4 text-sm outline-none placeholder:text-dim focus:border-pink/60"
            />
            {(searching || importing) && <Spinner className="absolute right-4 top-4.5 h-4 w-4 text-muted" />}
          </div>
          {linkMode ? (
            <button
              type="submit"
              disabled={importing}
              className="btn-label rounded-md border border-cream/60 px-6 py-4 text-cream hover:bg-cream/10 disabled:opacity-40"
            >
              Load playlist
            </button>
          ) : (
            <button
              type="button"
              onClick={start}
              disabled={seeds.length === 0}
              className="btn-label rounded-md bg-cream px-6 py-4 text-ink hover:bg-cream/85 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-pink disabled:cursor-not-allowed disabled:opacity-40"
            >
              Build the set
            </button>
          )}
        </form>

        {error && <p role="alert" className="text-sm text-pink">{error}</p>}
        {importMsg && (
          <p className="text-sm text-muted" aria-live="polite">
            {importMsg.text}{" "}
            {importMsg.connect && (
              <a href={api.loginUrl(typeof window === "undefined" ? "" : window.location.href)} className="text-pink underline">
                Connect Spotify
              </a>
            )}
          </p>
        )}
        {linkMode && !importing && !importMsg && (
          <p className="label text-dim">Press Enter to load the playlist&apos;s songs</p>
        )}

        {!full && !linkMode && results.length > 0 && (
          <PickList tracks={results} onPick={add} isSeed={isSeed} preview={preview} />
        )}

        {!searching && !error && !linkMode && query.trim().length >= 2 && results.length === 0 && (
          <p className="text-sm text-dim">No songs found. Only songs in the playlist and listening datasets are searchable.</p>
        )}
      </section>

      {source && importInfo && (
        <section aria-label="Pasted playlist" className="space-y-3 border-t border-line pt-6">
          <div className="flex items-start justify-between gap-3 text-sm">
            <p className="text-muted" aria-live="polite">
              {sourceTracks.length === 0 ? (
                <>None of the {importInfo.total} songs in “{source.name}” are in the dataset. Pick seeds by search instead.</>
              ) : (
                <>
                  Found {sourceTracks.length} of {importInfo.total} songs from{" "}
                  <span className="text-text">“{source.name}”</span>
                  {importInfo.loose > 0 && <span className="text-dim"> ({importInfo.loose} matched by title &amp; artist)</span>}
                  . Pick up to {MAX_START_SEEDS} as seeds.
                </>
              )}
            </p>
            <button
              onClick={() => {
                setSource(null);
                setSourceTracks([]);
                setImportInfo(null);
                setImportMsg(null);
              }}
              className="label shrink-0 text-dim hover:text-text"
            >
              Clear
            </button>
          </div>
          {sourceTracks.length > 0 && (
            <PickList tracks={sourceTracks} onPick={add} isSeed={isSeed} disabled={full} scroll preview={preview} />
          )}
          {importInfo.unmatched.length > 0 && (
            <details className="text-sm text-dim">
              <summary className="cursor-pointer hover:text-muted">
                {importInfo.missing} not in the dataset (too new or too rare)
              </summary>
              <ul className="mt-1 max-h-40 space-y-0.5 overflow-y-auto pl-4">
                {importInfo.unmatched.map((u) => (
                  <li key={u} className="truncate">{u}</li>
                ))}
                {importInfo.missing > importInfo.unmatched.length && (
                  <li>…and {importInfo.missing - importInfo.unmatched.length} more</li>
                )}
              </ul>
            </details>
          )}
        </section>
      )}

      {(seeds.length > 0 || source || latest) && (
        <div className="flex justify-end">
          <button
            onClick={startOver}
            className="label text-dim hover:text-text"
            title="Forget the picked seeds, the pasted playlist and your last set"
          >
            Start over
          </button>
        </div>
      )}
    </div>
  );
}

/** Song rows: preview one, or click it to pick it as a seed. Used for search results and pasted
 *  playlists. */
function PickList({
  tracks,
  onPick,
  isSeed,
  preview,
  disabled = false,
  scroll = false,
}: {
  tracks: Track[];
  onPick: (t: Track) => void;
  isSeed: (t: Track) => boolean;
  preview: ReturnType<typeof usePreview>;
  disabled?: boolean;
  scroll?: boolean;
}) {
  return (
    <ul className={`overflow-hidden rounded-md border border-line bg-panel/60 ${scroll ? "max-h-80 overflow-y-auto" : ""}`}>
      {tracks.map((t) => (
        <li key={t.track_uri} className="flex items-center gap-1 border-b border-line/60 pl-3 last:border-b-0">
          <PreviewButton name={t.name} state={preview.state(t.track_uri)} onClick={() => void preview.toggle(t.track_uri)} />
          <button
            onClick={() => onPick(t)}
            disabled={disabled || isSeed(t)}
            aria-pressed={isSeed(t)}
            className="flex min-w-0 flex-1 items-center gap-3 px-3 py-2.5 text-left hover:bg-raised/60 disabled:opacity-40"
          >
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-semibold">{t.name}</p>
              <p className="truncate text-xs text-muted">{t.artist}</p>
            </div>
            <span className="label shrink-0 text-dim tabular-nums">{formatCount(t.playlist_count)} playlists</span>
            <PlusIcon className="h-4 w-4 shrink-0 text-pink" />
          </button>
        </li>
      ))}
    </ul>
  );
}
