"use client";

// One 30-second preview player per screen (Seed search, Results).
//
// Every play asks the API for a FRESH preview URL (provider URLs are signed and expire, so none
// is reused). If the audio fails to load: ask again once (same source, new URL), then exclude
// that source so the API falls back to the next one (Deezer → iTunes). Each click logs one
// attempt with its final outcome (`make preview-report`). Tracks known to have no preview are
// remembered, from failed clicks or from `prefetch` (the API pre-resolves in the background).

import { useEffect, useRef, useState } from "react";

import type { PreviewState } from "@/components/track-row";
import { api, type PreviewAvailability, type PreviewOutcome, type PreviewSource } from "./api";
import { currentSession } from "./session";

const MAX_TRIES = 4; // first try + one retry per source (2 sources)

/** Deezer URLs carry their expiry (`exp=<unix seconds>`): a past one means "expired". */
export function isExpired(url: string, now = Date.now()): boolean {
  const exp = url.match(/[?&~=]exp=(\d+)/)?.[1]; // Deezer: ?hdnea=exp=<unix>~acl=…
  return exp !== undefined && Number(exp) * 1000 < now;
}

export type PreviewInfo = PreviewAvailability;

export function usePreview(onPlay?: (uri: string) => void) {
  const [playing, setPlaying] = useState<string | null>(null);
  const [loading, setLoading] = useState<string | null>(null);
  const [info, setInfo] = useState<Map<string, PreviewInfo>>(new Map());
  const audio = useRef<HTMLAudioElement | null>(null);
  const click = useRef(0); // newest click wins; older ones stop quietly

  // Stop when the screen goes away (or is hidden by Next's kept-alive <Activity>).
  useEffect(() => {
    const el = audio;
    return () => el.current?.pause();
  }, []);

  function remember(uri: string, i: PreviewInfo) {
    setInfo((m) => new Map(m).set(uri, i));
  }

  function stop() {
    click.current++;
    audio.current?.pause();
    setPlaying(null);
    setLoading(null);
  }

  /** Load and start `url`; false if the browser couldn't play it. */
  async function tryPlay(url: string): Promise<boolean> {
    if (!audio.current) {
      audio.current = new Audio();
      audio.current.onended = () => setPlaying(null);
    }
    const el = audio.current;
    el.onerror = null;
    el.src = url;
    try {
      await el.play();
      // An error after it started (stream dropped): just show it stopped.
      el.onerror = () => setPlaying(null);
      return true;
    } catch {
      return false;
    }
  }

  async function toggle(uri: string) {
    if (playing === uri || loading === uri) return stop();
    stop();
    const id = click.current;
    setLoading(uri);
    const steps: string[] = [];
    const exclude: PreviewSource[] = [];
    let retried = false;
    let outcome: PreviewOutcome = "no_match";
    let source: PreviewSource | null = null;
    for (let i = 0; i < MAX_TRIES; i++) {
      let res;
      try {
        res = await api.preview(uri, exclude);
      } catch {
        if (steps.length === 0) outcome = "source_error";
        break;
      }
      if (id !== click.current) return;
      if (!res.preview_url) {
        // Nothing (more) to try. After playback failures, those are the real reason.
        if (steps.length === 0) outcome = res.outcome;
        break;
      }
      const played = await tryPlay(res.preview_url);
      if (id !== click.current) return;
      if (played) {
        steps.push(`${res.source}:ok`);
        setLoading(null);
        setPlaying(uri);
        remember(uri, { status: "yes", source: res.source, open_url: res.open_url });
        log(uri, "ok", res.source, steps);
        onPlay?.(uri);
        return;
      }
      outcome = isExpired(res.preview_url) ? "expired_url" : "playback_error";
      source = res.source;
      steps.push(`${res.source}:${outcome}`);
      if (!retried) {
        retried = true; // same source once more, with a fresh URL
      } else if (res.source) {
        exclude.push(res.source); // then the next source
        retried = false;
      }
    }
    if (id !== click.current) return;
    setLoading(null);
    if (outcome !== "source_error") remember(uri, { status: "no", source: null, open_url: null });
    log(uri, outcome, source, steps);
  }

  function log(uri: string, outcome: PreviewOutcome, source: PreviewSource | null, steps: string[]) {
    void api.logPreviewAttempt({
      track_uri: uri,
      outcome,
      source,
      steps: steps.length ? steps.join(">") : null,
      session_id: currentSession(),
    });
  }

  /** Ask the API to pre-resolve these tracks in the background; marks the ones with no preview. */
  async function prefetch(uris: string[]) {
    const todo = uris.filter((u) => !info.has(u)).slice(0, 50);
    if (todo.length === 0) return;
    try {
      const found = await api.resolvePreviews(todo);
      setInfo((m) => {
        const n = new Map(m);
        for (const [u, a] of Object.entries(found)) if (a.status !== "unknown" && !n.has(u)) n.set(u, a);
        return n;
      });
    } catch {
      /* best effort: the play button still works */
    }
  }

  function state(uri: string): PreviewState {
    if (loading === uri) return "loading";
    if (playing === uri) return "playing";
    if (info.get(uri)?.status === "no") return "unavailable";
    return "idle";
  }

  return { toggle, stop, state, prefetch, info: (uri: string) => info.get(uri), playing };
}
