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
import { clearAll, readResults, writeResults, type SavedPlaylist } from "@/lib/stored";
import { openTrackUrl, useProvider } from "@/lib/provider";
import { usePreview } from "@/lib/use-preview";
import { Spinner, XIcon } from "./icons";
import { Loading, Ticker } from "./night";
import ExportPanel from "./export-panel";
import SavePanel from "./save-panel";
import TrackRow from "./track-row";

type Status = "loading" | "ready" | "error";

export default function ResultsScreen() {
  const router = useRouter();
  const params = useSearchParams();

  // Saved vibe (refresh, new tab, Spotify login redirect). Browser-only component, so
  // reading localStorage in initialisers is safe (see results-client.tsx).
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
  // A re-rank (slider, more like this, seed removed) is in flight: the old list stays, dimmed.
  const [reranking, setReranking] = useState(false);
  // "More like this": the song being mixed in, then what the mix brought (shown for a few seconds).
  const [mixing, setMixing] = useState<Recommendation | null>(null);
  const [mixed, setMixed] = useState<{ seed: string; name: string; uris: Set<string> } | null>(null);
  const mixedTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(mixedTimer.current), []);
  const [name, setName] = useState(() => restored?.name ?? "");
  const [description, setDescription] = useState(() => restored?.description ?? "Built with Swave");
  const [cover, setCover] = useState<string | null>(() => restored?.cover ?? null);
  // Playlist the vibe was imported from (set by the Seed screen): offers "Add to <name>".
  const [source] = useState(() => readSource(params.get("run")));
  // …and "Hide songs already in <name>" (on by default; every version of those songs).
  const [hideSource, setHideSource] = useState(() => restored?.hideSource ?? true);
  const hiddenSongs = hideSource ? (source?.trackUris ?? []) : [];
  // "Previewable only" (off by default): hide songs known to have no preview anywhere.
  const [previewOnly, setPreviewOnly] = useState(false);
  const provider = useProvider();
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

  const session = useRef<string>("");
  const requestId = useRef(0);
  // Latest values for async callbacks without re-creating them (updated after each render).
  const latest = useRef({ list, kept, removed, seeds, slider, hiddenSongs });
  useLayoutEffect(() => {
    latest.current = { list, kept, removed, seeds, slider, hiddenSongs };
  });

  const log = useCallback((events: Omit<AppEvent, "session_id" | "slider">[]) => {
    api.log(events.map((e) => ({ ...e, session_id: session.current, slider: latest.current.slider })));
  }, []);
  const preview = usePreview((uri) =>
    log([{ action: "previewed", track_uri: uri,
           position: latest.current.list.findIndex((x) => x.track_uri === uri) }]),
  );

  // Pre-resolve previews in the background: the top 20 whenever the list changes (all of it
  // with "Previewable only"), so play starts fast and songs without one show a link instead.
  const prefetchPreviews = preview.prefetch;
  const prefetchKey = list.slice(0, previewOnly ? list.length : 20).map((t) => t.track_uri).join(",");
  useEffect(() => {
    if (prefetchKey) void prefetchPreviews(prefetchKey.split(","));
    // prefetchPreviews changes every render; the key is what matters
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefetchKey]);

  /** Re-rank: kept tracks stay on top, fresh recommendations fill the rest. */
  const load = useCallback(
    async (nextSeeds: string[], nextSlider: number, mix?: Recommendation) => {
      const id = ++requestId.current;
      setReranking(true);
      clearTimeout(mixedTimer.current);
      setMixed(null);
      const { list: cur, kept: k, removed: r, hiddenSongs: hidden } = latest.current;
      const keptTracks = cur.filter((t) => k.has(t.track_uri));
      try {
        // Kept tracks are not excluded, so they come back with up-to-date reasons.
        const res = await api.recommend({
          seeds: nextSeeds,
          adventure: clampSlider(nextSlider),
          exclude: [...r],
          exclude_songs: hidden,
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
        if (mix) {
          // Songs the mix brought in: they slide in tagged NEW for a few seconds.
          const before = new Set(cur.map((t) => t.track_uri));
          const uris = new Set(merged.filter((t) => !before.has(t.track_uri)).map((t) => t.track_uri));
          setMixed({ seed: mix.track_uri, name: mix.name, uris });
          mixedTimer.current = setTimeout(() => setMixed(null), 6000);
        }
      } catch (err) {
        if (id !== requestId.current) return;
        setError(err instanceof ApiError ? err.message : "Couldn't load recommendations");
        setStatus("error");
      } finally {
        // Only the newest request clears it; a superseded one leaves it to its successor.
        if (id === requestId.current) {
          setReranking(false);
          setMixing(null);
        }
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Save the vibe after every change (localStorage: survives refresh, new tabs, restarts).
  useEffect(() => {
    if (status !== "ready" || list.length === 0) return;
    writeResults({
      run, seeds, slider, list, kept: [...kept], removed: [...removed], seedTracks,
      name, description, cover, alpha, saved, hideSource,
    });
  }, [status, run, seeds, slider, list, kept, removed, seedTracks, name, description, cover, alpha, saved, hideSource]);

  function toggleHideSource(on: boolean) {
    setHideSource(on);
    latest.current.hiddenSongs = on ? (source?.trackUris ?? []) : [];
    void load(latest.current.seeds, latest.current.slider);
  }

  function startOver() {
    preview.stop();
    clearAll();
    // `fresh` remounts the (kept-alive) Seed screen so it starts empty; see seed-client.tsx.
    router.push(`/?fresh=${Date.now().toString(36)}`);
  }

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
    if (preview.playing === t.track_uri) preview.stop();
    let replacement: Recommendation | undefined;
    try {
      const res = await api.recommend({
        seeds,
        alpha,
        exclude: [...latest.current.list.map((x) => x.track_uri), ...nextRemoved],
        exclude_songs: hiddenSongs,
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
    if (preview.playing === t.track_uri) preview.stop();
    setMixing(t);
    void load(next, slider, t);
  }

  function removeSeed(uri: string) {
    if (seeds.length <= 1) return;
    const next = seeds.filter((s) => s !== uri);
    setSeeds(next);
    void load(next, slider);
  }

  if (seeds.length === 0) {
    return (
      <div className="space-y-4">
        <p className="text-muted">No seeds picked yet.</p>
        <Link href="/" className="text-pink hover:underline">← Pick some songs</Link>
      </div>
    );
  }

  const fill = `${Math.round(slider * 100)}%`;
  const noPreview = list.filter((t) => preview.state(t.track_uri) === "unavailable").length;
  // Entrance order of the songs a mix brought in (top to bottom).
  const newOrder = new Map(list.filter((t) => mixed?.uris.has(t.track_uri)).map((t, k) => [t.track_uri, k]));

  return (
    <div className="space-y-7">
      <div className="flex items-center justify-between gap-4">
        {/* `fresh` remounts the kept-alive Seed screen so it re-reads the saved draft and vibe. */}
        <button onClick={() => router.push(`/?fresh=${Date.now().toString(36)}`)} className="label text-muted hover:text-text">
          ← Edit seeds
        </button>
        <button
          onClick={startOver}
          className="label text-dim hover:text-text"
          title="Forget this set, its kept songs and the pasted playlist"
        >
          Start over
        </button>
      </div>

      {mixing ? (
        <Ticker key="mixing" lead="Mixing in" items={[mixing.name, mixing.artist]} empty="" />
      ) : (
        <Ticker items={seedTracks.map((s) => s.name)} empty="Now loaded ▸ tuning in" />
      )}

      <section aria-label="Seeds" className="flex flex-wrap gap-2">
        {seedTracks.map((s, i) => (
          <span
            key={s.track_uri}
            className={`flex items-center gap-2 rounded-md border py-1.5 pl-2.5 pr-1 text-xs ${
              mixed?.seed === s.track_uri ? "chip-pop border-pink bg-pink/10" : "border-line bg-panel"
            }`}
          >
            <span className="font-mono text-pink">{String(i + 1).padStart(2, "0")}</span>
            <span className="max-w-48 truncate font-semibold" title={`${s.name} · ${s.artist}`}>{s.name}</span>
            <button
              onClick={() => removeSeed(s.track_uri)}
              disabled={seeds.length <= 1}
              className="rounded p-1 text-muted hover:bg-raised hover:text-text disabled:invisible"
              aria-label={`Remove seed ${s.name}`}
            >
              <XIcon className="h-3 w-3" />
            </button>
          </span>
        ))}
      </section>

      <section className="flex flex-wrap items-end justify-between gap-x-8 gap-y-4">
        <div className="flex min-w-0 items-center gap-4">
          <h1 className="shrink-0 font-display text-xl tracking-[0.18em]">The set</h1>
          {mixing && status === "ready" ? (
            <span role="status" className="label flex min-w-0 max-w-64 items-center gap-2 text-pink sm:max-w-72">
              <Spinner className="h-3.5 w-4 shrink-0" />
              <span className="truncate">Mixing in “{mixing.name}”</span>
            </span>
          ) : reranking && status === "ready" ? (
            <span role="status" className="label dots flex items-center gap-2 text-pink">
              <Spinner className="h-3.5 w-4" />
              Re-cutting
            </span>
          ) : mixed ? (
            <span role="status" className="chip-pop label flex min-w-0 max-w-64 items-center gap-2 text-cream sm:max-w-72">
              {mixed.uris.size > 0 ? (
                // The new songs can be far down the list: jump to the first one.
                <button
                  onClick={() => document.querySelector(".mix-new")?.scrollIntoView({ behavior: "smooth", block: "center" })}
                  className="shrink-0 rounded-sm border border-pink/60 px-1.5 py-0.5 text-pink hover:bg-pink/10"
                >
                  +{mixed.uris.size} new ↓
                </button>
              ) : (
                <span className="shrink-0 text-pink">Mixed in</span>
              )}
              <span className="truncate">like “{mixed.name}”</span>
            </span>
          ) : null}
        </div>
        <div className="w-full space-y-2 sm:w-80">
          <label htmlFor="vibe" className="label flex justify-between text-muted">
            <span>Familiar</span>
            <span className="text-pink tabular-nums">{String(Math.round(slider * 100)).padStart(3, "0")}</span>
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
            className="slider w-full"
            style={{ "--fill": fill } as React.CSSProperties}
            aria-valuetext={`${Math.round(slider * 100)}% adventurous`}
          />
        </div>
      </section>

      <div className="flex flex-wrap gap-x-6 gap-y-2">
        {source && (source.trackUris?.length ?? 0) > 0 && (
          <label className="flex items-center gap-2 text-sm text-muted">
            <input type="checkbox" checked={hideSource} onChange={(e) => toggleHideSource(e.target.checked)} />
            Hide songs already in “{source.name}”
            <span className="text-dim">({source.trackUris?.length})</span>
          </label>
        )}
        <label className="flex items-center gap-2 text-sm text-muted">
          <input type="checkbox" checked={previewOnly} onChange={(e) => setPreviewOnly(e.target.checked)} />
          Previewable only
          {previewOnly && noPreview > 0 && <span className="text-dim">({noPreview} hidden)</span>}
        </label>
      </div>

      {notice && (
        <p className="flex items-center justify-between rounded-md border border-line bg-panel px-3 py-2 text-sm text-muted">
          {notice}
          <button onClick={() => setNotice(null)} aria-label="Dismiss" className="p-1 text-dim hover:text-text">
            <XIcon className="h-3.5 w-3.5" />
          </button>
        </p>
      )}

      {status === "loading" && <Loading label="Cutting your set" />}

      {status === "error" && (
        <div role="alert" className="space-y-3 rounded-md border border-pink/40 bg-pink/5 p-4">
          <p className="text-pink-soft">{error}</p>
          <button
            onClick={() => {
              setStatus("loading");
              void load(seeds, slider);
            }}
            className="label text-text underline"
          >
            Try again
          </button>
        </div>
      )}

      {status === "ready" && list.length === 0 && (
        <p className="py-12 text-dim">No recommendations for these seeds. Try different songs.</p>
      )}

      {status === "ready" && list.length > 0 && (
        // While mixing, a pink scan line sweeps down the list (the wrapper clips it).
        <div className="relative overflow-hidden">
          {mixing && <div className="mix-sweep" aria-hidden="true" />}
          <ol
            className={`border-t border-line/70 transition-opacity duration-300 ${
              mixing ? "mixing pointer-events-none" : reranking ? "pointer-events-none opacity-50" : ""
            }`}
            aria-busy={reranking}
          >
            {list.map((t, i) => previewOnly && preview.state(t.track_uri) === "unavailable" ? null : (
              <TrackRow
                key={t.track_uri}
                track={t}
                position={i}
                kept={kept.has(t.track_uri)}
                pending={pending.has(t.track_uri)}
                preview={preview.state(t.track_uri)}
                previewInfo={preview.info(t.track_uri)}
                open={{ href: openTrackUrl(provider.name, t.track_uri), label: provider.info.label }}
                canAddSeed={seeds.length < MAX_SEEDS}
                onPreview={() => void preview.toggle(t.track_uri)}
                onKeep={() => toggleKeep(t, i)}
                onMore={() => moreLikeThis(t, i)}
                onRemove={() => void remove(t, i)}
                mix={mixing?.track_uri === t.track_uri ? "source" : mixed?.uris.has(t.track_uri) ? "new" : undefined}
                mixOrder={newOrder.get(t.track_uri)}
              />
            ))}
          </ol>
        </div>
      )}

      {status === "ready" && list.length > 0 && (provider.info.can_write ? (
        <SavePanel
          tracks={list.filter((t) => kept.has(t.track_uri))}
          details={{ name, description, cover }}
          onDetails={(d) => {
            if (d.name !== undefined) setName(d.name);
            if (d.description !== undefined) setDescription(d.description);
            if (d.cover !== undefined) setCover(d.cover);
          }}
          source={source?.provider === "paste" ? null : source}
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
      ) : (
        <ExportPanel
          tracks={list.filter((t) => kept.has(t.track_uri))}
          name={name}
          onName={setName}
          provider={provider.info}
        />
      ))}
    </div>
  );
}
