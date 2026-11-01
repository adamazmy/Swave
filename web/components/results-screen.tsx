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
  replaceAt,
  sliderToAlpha,
} from "@/lib/playlist";
import { currentSession } from "@/lib/session";
import { Spinner, XIcon } from "./icons";
import TrackRow, { type PreviewState } from "./track-row";

type Status = "loading" | "ready" | "error";

export default function ResultsScreen() {
  const router = useRouter();
  const params = useSearchParams();

  const [seeds, setSeeds] = useState<string[]>(() => parseSeeds(params.get("seeds")));
  const [slider, setSlider] = useState(() => {
    const v = Number(params.get("v"));
    return Number.isFinite(v) ? sliderToAlpha(v) : 0;
  });
  const [seedTracks, setSeedTracks] = useState<Track[]>([]);
  const [list, setList] = useState<Recommendation[]>([]);
  const [kept, setKept] = useState<Set<string>>(new Set());
  const [removed, setRemoved] = useState<Set<string>>(new Set());
  const [pending, setPending] = useState<Set<string>>(new Set());
  const [status, setStatus] = useState<Status>(() => (seeds.length ? "loading" : "ready"));
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

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
        const res = await api.recommend({
          seeds: nextSeeds,
          alpha: sliderToAlpha(nextSlider),
          exclude: [...r, ...k],
          n: Math.max(1, LIST_SIZE - keptTracks.length),
        });
        if (id !== requestId.current) return; // a newer request superseded this one
        const merged = mergeKept(keptTracks, res.results);
        setList(merged);
        setSeedTracks(res.seeds);
        setError(null);
        setStatus("ready");
        if (res.unknown_seeds.length) setNotice(`${res.unknown_seeds.length} seed(s) aren't in the dataset and were ignored.`);
        log(
          merged
            .map((t, i) => ({ action: "shown" as const, track_uri: t.track_uri, position: i }))
            .filter((e) => !k.has(e.track_uri)),
        );
        router.replace(`/results?seeds=${nextSeeds.join(",")}&v=${nextSlider}`, { scroll: false });
      } catch (err) {
        if (id !== requestId.current) return;
        setError(err instanceof ApiError ? err.message : "Couldn't load recommendations");
        setStatus("error");
      }
    },
    [log, router],
  );

  // First load.
  useEffect(() => {
    session.current = currentSession();
    if (seeds.length) void load(seeds, slider);
    const el = audio;
    return () => el.current?.pause();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Slider: debounce re-ranks while dragging.
  const firstSlider = useRef(true);
  useEffect(() => {
    if (firstSlider.current) {
      firstSlider.current = false;
      return;
    }
    const t = setTimeout(() => void load(latest.current.seeds, slider), 350);
    return () => clearTimeout(t);
  }, [slider, load]);

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
        alpha: sliderToAlpha(slider),
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
        <div className="flex flex-col items-end gap-1">
          <button
            disabled
            className="rounded-lg bg-emerald-500 px-4 py-2 text-sm font-medium text-zinc-950 disabled:cursor-not-allowed disabled:bg-zinc-800 disabled:text-zinc-500"
            title="Spotify save arrives in M4"
          >
            Save {kept.size > 0 ? kept.size : ""} to Spotify
          </button>
          <span className="text-xs text-zinc-500">{kept.size} kept · Spotify save coming in M4</span>
        </div>
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
          onChange={(e) => setSlider(Number(e.target.value))}
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
    </div>
  );
}
