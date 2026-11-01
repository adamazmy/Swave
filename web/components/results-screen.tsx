"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { api, ApiError, type AppEvent, type Recommendation, type Track } from "@/lib/api";
import {
  addSeed,
  LIST_SIZE,
  MAX_SEEDS,
  mergeKept,
  parseSeeds,
  refreshKept,
  replaceAt,
  clampSlider,
} from "@/lib/playlist";
import { currentSession } from "@/lib/session";
import { readSource } from "@/lib/source";
import { readResults, writeResults, type SavedPlaylist } from "@/lib/stored";
import { Spinner, XIcon } from "./icons";
import SavePanel from "./save-panel";
import TrackRow, { type PreviewState } from "./track-row";

type Status = "loading" | "ready" | "error";

export default function ResultsScreen() {
  const router = useRouter();
  const params = useSearchParams();

  // State saved for this tab (reload / Spotify login redirect). Browser-only component, so
  // reading sessionStorage in initialisers is safe (see results-client.tsx).
  const [restored] = useState(() => readResults(parseSeeds(params.get("seeds"))));

  const [seeds, setSeeds] = useState<string[]>(() => restored?.seeds ?? parseSeeds(params.get("seeds")));
  const [slider, setSlider] = useState(() => {
    if (restored) return restored.slider;
    const v = Number(params.get("v"));
    return Number.isFinite(v) ? clampSlider(v) : 0;
  });
  const [seedTracks, setSeedTracks] = useState<Track[]>(() => restored?.seedTracks ?? []);
  const [list, setList] = useState<Recommendation[]>(() => restored?.list ?? []);
  const [kept, setKept] = useState<Set<string>>(() => new Set(restored?.kept));
  const [removed, setRemoved] = useState<Set<string>>(() => new Set(restored?.removed));
  const [pending, setPending] = useState<Set<string>>(new Set());
  const [status, setStatus] = useState<Status>(() => (restored || !seeds.length ? "ready" : "loading"));
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState(() => restored?.name ?? "");
  const [description, setDescription] = useState(() => restored?.description ?? "Built with Swave");
  const [cover, setCover] = useState<string | null>(() => restored?.cover ?? null);
  // Playlist the vibe was imported from (set by the Seed screen): offers "Add to <name>".
  const [source] = useState(() => readSource(params.get("run")));
  // Alpha the API picked for the current list: replacements must use the same one.
  const [alpha, setAlpha] = useState(() => restored?.alpha ?? 0);
  // Playlist this vibe was saved to: the next save updates it instead of making a duplicate.
  const [saved, setSaved] = useState<SavedPlaylist | null>(() => restored?.saved ?? null);
  // Each new vibe has its own run id (see results-client.tsx); keep it in every URL we write.
  const run = params.get("run");
  const resultsUrl = useCallback(
    (s: string[], v: number) => `/results?seeds=${s.join(",")}&v=${v}${run ? `&run=${run}` : ""}`,
    [run],
  );
  // Coming back from the Spotify login redirect.
  const [notice, setNotice] = useState<string | null>(() => {
    if (params.get("spotify") === "connected") return "Spotify connected. You can save your playlist now.";
    const err = params.get("spotify_error");
    return err ? `Spotify login failed: ${err}` : null;
  });

  const [playing, setPlaying] = useState<string | null>(null);
  const [loadingPreview, setLoadingPreview] = useState<string | null>(null);
  const [previews, setPreviews] = useState<Map<string, string | null>>(new Map());
  const audio = useRef<HTMLAudioElement | null>(null);

  const session = useRef<string>("");
  const requestId = useRef(0);
  // Latest values for async callbacks without re-creating them (updated after each render).
  const latest = useRef({ list, kept, removed, seeds, slider });
  useLayoutEffect(() => {
    latest.current = { list, kept, removed, seeds, slider };
  });

  const log = useCallback((events: Omit<AppEvent, "session_id" | "slider">[]) => {
    api.log(events.map((e) => ({ ...e, session_id: session.current, slider: latest.current.slider })));
  }, []);

  /** Re-rank: kept tracks stay on top, fresh recommendations fill the rest. */
  const load = useCallback(
    async (nextSeeds: string[], nextSlider: number) => {
      const id = ++requestId.current;
      const { list: cur, kept: k, removed: r } = latest.current;
      const keptTracks = cur.filter((t) => k.has(t.track_uri));
      try {
        // Kept tracks are not excluded, so they come back with up-to-date reasons.
        const res = await api.recommend({
          seeds: nextSeeds,
          adventure: clampSlider(nextSlider),
          exclude: [...r],
          n: Math.min(100, LIST_SIZE + keptTracks.length),
        });
        if (id !== requestId.current) return; // a newer request superseded this one
        const merged = mergeKept(refreshKept(keptTracks, res.results, nextSeeds), res.results);
        setList(merged);
        setSeedTracks(res.seeds);
        setAlpha(res.alpha);
        setName((n) => n || `Swave · ${res.seeds[0]?.name ?? "my vibe"}`);
        setError(null);
        setStatus("ready");
        if (res.unknown_seeds.length) setNotice(`${res.unknown_seeds.length} seed(s) aren't in the dataset and were ignored.`);
        log(
          merged
            .map((t, i) => ({ action: "shown" as const, track_uri: t.track_uri, position: i }))
            .filter((e) => !k.has(e.track_uri)),
        );
        router.replace(resultsUrl(nextSeeds, nextSlider), { scroll: false });
      } catch (err) {
        if (id !== requestId.current) return;
        setError(err instanceof ApiError ? err.message : "Couldn't load recommendations");
        setStatus("error");
      }
    },
    [log, router, resultsUrl],
  );

  // First load. Skipped when the list is already there: restored from the tab, or this page
  // was only hidden by Next's <Activity> and is being shown again (effects re-run then).
  useEffect(() => {
    session.current = currentSession();
    if (latest.current.list.length === 0 && seeds.length) void load(seeds, slider);
    else if (params.has("spotify") || params.has("spotify_error")) {
      router.replace(resultsUrl(seeds, slider), { scroll: false });
    }
    const el = audio;
    return () => el.current?.pause();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Save tab state after every change.
  useEffect(() => {
    if (status !== "ready" || list.length === 0) return;
    writeResults({
      seeds, slider, list, kept: [...kept], removed: [...removed], seedTracks,
      name, description, cover, alpha, saved,
    });
  }, [status, seeds, slider, list, kept, removed, seedTracks, name, description, cover, alpha, saved]);

  // Slider: re-rank from the change handler (debounced while dragging). Not an effect: effects
  // also run on mount (twice in dev StrictMode), which caused an unwanted re-rank on open.
  const sliderTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(sliderTimer.current), []);
  function onSlider(value: number) {
    setSlider(value);
    clearTimeout(sliderTimer.current);
    sliderTimer.current = setTimeout(() => void load(latest.current.seeds, value), 350);
  }

  function toggleKeep(t: Recommendation, i: number) {
    const next = new Set(kept);
    if (next.has(t.track_uri)) next.delete(t.track_uri);
    else {
      next.add(t.track_uri);
      log([{ action: "kept", track_uri: t.track_uri, position: i }]);
    }
    setKept(next);
  }

  async function remove(t: Recommendation, i: number) {
    log([{ action: "removed", track_uri: t.track_uri, position: i }]);
    const nextRemoved = new Set(removed).add(t.track_uri);
    setRemoved(nextRemoved);
    const nextKept = new Set(kept);
    nextKept.delete(t.track_uri);
    setKept(nextKept);
    setPending((p) => new Set(p).add(t.track_uri));
    if (playing === t.track_uri) stopAudio();
    let replacement: Recommendation | undefined;
    try {
      const res = await api.recommend({
        seeds,
        alpha,
        exclude: [...latest.current.list.map((x) => x.track_uri), ...nextRemoved],
        n: 1,
      });
      replacement = res.results[0];
    } catch {
      /* no replacement: just drop the row */
    }
    setList((prev) => {
      const idx = prev.findIndex((x) => x.track_uri === t.track_uri);
      if (idx === -1) return prev;
      const dup = replacement && prev.some((x) => x.track_uri === replacement!.track_uri);
      return replaceAt(prev, idx, dup ? undefined : replacement);
    });
    setPending((p) => {
      const n = new Set(p);
      n.delete(t.track_uri);
      return n;
    });
    if (replacement) log([{ action: "shown", track_uri: replacement.track_uri, position: i }]);
  }

  function moreLikeThis(t: Recommendation, i: number) {
    const next = addSeed(seeds, t.track_uri);
    if (next === seeds) {
      setNotice(`You can use up to ${MAX_SEEDS} seeds.`);
      return;
    }
    log([{ action: "more_like_this", track_uri: t.track_uri, position: i }]);
    // A song you want more of is a song you like: keep it so it stays in the list.
    const nextKept = new Set(kept).add(t.track_uri);
    setKept(nextKept);
    latest.current.kept = nextKept;
    setSeeds(next);
    void load(next, slider);
  }

  function removeSeed(uri: string) {
    if (seeds.length <= 1) return;
    const next = seeds.filter((s) => s !== uri);
    setSeeds(next);
    void load(next, slider);
  }

  function stopAudio() {
    audio.current?.pause();
    setPlaying(null);
  }

  async function togglePreview(t: Recommendation, i: number) {
    if (playing === t.track_uri) return stopAudio();
    let url = previews.get(t.track_uri);
    if (url === undefined) {
      setLoadingPreview(t.track_uri);
      try {
        url = (await api.preview(t.track_uri)).preview_url;
      } catch {
        url = null;
      } finally {
        setLoadingPreview(null);
      }
      setPreviews((m) => new Map(m).set(t.track_uri, url ?? null));
    }
    if (!url) return;
    if (!audio.current) {
      audio.current = new Audio();
      audio.current.onended = () => setPlaying(null);
    }
    const el = audio.current;
    el.onerror = () => {
      // Signed URL expired: forget it so the next click fetches a fresh one.
      setPreviews((m) => {
        const n = new Map(m);
        n.delete(t.track_uri);
        return n;
      });
      setPlaying(null);
    };
    el.src = url;
    try {
      await el.play();
      setPlaying(t.track_uri);
      log([{ action: "previewed", track_uri: t.track_uri, position: i }]);
    } catch {
      setPlaying(null);
    }
  }

  function previewState(uri: string): PreviewState {
    if (loadingPreview === uri) return "loading";
    if (playing === uri) return "playing";
    if (previews.get(uri) === null) return "unavailable";
    return "idle";
  }

  if (seeds.length === 0) {
    return (
      <div className="space-y-4">
        <p className="text-zinc-400">No seeds picked yet.</p>
        <Link href="/" className="text-emerald-400 hover:underline">← Pick some songs</Link>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <header className="flex items-start justify-between gap-4">
        <div className="space-y-1">
          <Link href="/" className="text-sm text-zinc-500 hover:text-zinc-300">← New vibe</Link>
          <h1 className="text-2xl font-semibold tracking-tight">Your vibe</h1>
        </div>
        <span className="pt-6 text-sm text-zinc-400 tabular-nums">{kept.size} kept</span>
      </header>

      <section aria-label="Seeds" className="flex flex-wrap gap-2">
        {seedTracks.map((s) => (
          <span key={s.track_uri} className="flex items-center gap-1.5 rounded-full bg-zinc-800 py-1 pl-3 pr-1 text-sm">
            <span className="max-w-56 truncate">
              {s.name} <span className="text-zinc-500">· {s.artist}</span>
            </span>
            <button
              onClick={() => removeSeed(s.track_uri)}
              disabled={seeds.length <= 1}
              className="rounded-full p-1 text-zinc-400 hover:bg-white/10 disabled:invisible"
              aria-label={`Remove seed ${s.name}`}
            >
              <XIcon className="h-3.5 w-3.5" />
            </button>
          </span>
        ))}
      </section>

      <section className="space-y-2">
        <label htmlFor="vibe" className="flex justify-between text-sm text-zinc-400">
          <span>Familiar</span>
          <span>Adventurous</span>
        </label>
        <input
          id="vibe"
          type="range"
          min={0}
          max={1}
          step={0.05}
          value={slider}
          onChange={(e) => onSlider(Number(e.target.value))}
          className="w-full"
          aria-valuetext={`${Math.round(slider * 100)}% adventurous`}
        />
      </section>

      {notice && (
        <p className="flex items-center justify-between rounded-lg bg-zinc-900 px-3 py-2 text-sm text-zinc-300">
          {notice}
          <button onClick={() => setNotice(null)} aria-label="Dismiss" className="p-1 text-zinc-500 hover:text-zinc-200">
            <XIcon className="h-3.5 w-3.5" />
          </button>
        </p>
      )}

      {status === "loading" && (
        <div className="flex items-center gap-2 py-12 text-zinc-500">
          <Spinner className="h-5 w-5" /> Finding songs…
        </div>
      )}

      {status === "error" && (
        <div role="alert" className="space-y-3 rounded-lg border border-red-500/30 bg-red-500/5 p-4">
          <p className="text-red-300">{error}</p>
          <button
            onClick={() => {
              setStatus("loading");
              void load(seeds, slider);
            }}
            className="text-sm text-zinc-300 underline"
          >
            Try again
          </button>
        </div>
      )}

      {status === "ready" && list.length === 0 && (
        <p className="py-12 text-zinc-500">No recommendations for these seeds. Try different songs.</p>
      )}

      {status === "ready" && list.length > 0 && (
        <ol className="space-y-1">
          {list.map((t, i) => (
            <TrackRow
              key={t.track_uri}
              track={t}
              position={i}
              kept={kept.has(t.track_uri)}
              pending={pending.has(t.track_uri)}
              preview={previewState(t.track_uri)}
              canAddSeed={seeds.length < MAX_SEEDS}
              onPreview={() => void togglePreview(t, i)}
              onKeep={() => toggleKeep(t, i)}
              onMore={() => moreLikeThis(t, i)}
              onRemove={() => void remove(t, i)}
            />
          ))}
        </ol>
      )}

      {status === "ready" && list.length > 0 && (
        <SavePanel
          tracks={list.filter((t) => kept.has(t.track_uri))}
          details={{ name, description, cover }}
          onDetails={(d) => {
            if (d.name !== undefined) setName(d.name);
            if (d.description !== undefined) setDescription(d.description);
            if (d.cover !== undefined) setCover(d.cover);
          }}
          source={source}
          saved={saved}
          onSaved={(playlist) => {
            if (playlist) setSaved(playlist);
            log(
              list
                .map((t, i) => ({ action: "saved" as const, track_uri: t.track_uri, position: i }))
                .filter((e) => kept.has(e.track_uri)),
            );
          }}
        />
      )}
    </div>
  );
}
