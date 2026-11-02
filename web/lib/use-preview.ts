"use client";

// One 30-second preview player per screen: fetch the (Deezer) preview URL on first play, cache it,
// toggle play/pause, remember tracks without a preview. Used by Seed (search) and Results.

import { useEffect, useRef, useState } from "react";

import type { PreviewState } from "@/components/track-row";
import { api } from "./api";

export function usePreview(onPlay?: (uri: string) => void) {
  const [playing, setPlaying] = useState<string | null>(null);
  const [loading, setLoading] = useState<string | null>(null);
  const [urls, setUrls] = useState<Map<string, string | null>>(new Map());
  const audio = useRef<HTMLAudioElement | null>(null);

  // Stop when the screen goes away (or is hidden by Next's kept-alive <Activity>).
  useEffect(() => {
    const el = audio;
    return () => el.current?.pause();
  }, []);

  function stop() {
    audio.current?.pause();
    setPlaying(null);
  }

  async function toggle(uri: string) {
    if (playing === uri) return stop();
    let url = urls.get(uri);
    if (url === undefined) {
      setLoading(uri);
      try {
        url = (await api.preview(uri)).preview_url;
      } catch {
        url = null;
      } finally {
        setLoading(null);
      }
      setUrls((m) => new Map(m).set(uri, url ?? null));
    }
    if (!url) return;
    if (!audio.current) {
      audio.current = new Audio();
      audio.current.onended = () => setPlaying(null);
    }
    const el = audio.current;
    el.onerror = () => {
      // Signed URL expired: forget it so the next click fetches a fresh one.
      setUrls((m) => {
        const n = new Map(m);
        n.delete(uri);
        return n;
      });
      setPlaying(null);
    };
    el.src = url;
    try {
      await el.play();
      setPlaying(uri);
      onPlay?.(uri);
    } catch {
      setPlaying(null);
    }
  }

  function state(uri: string): PreviewState {
    if (loading === uri) return "loading";
    if (playing === uri) return "playing";
    if (urls.get(uri) === null) return "unavailable";
    return "idle";
  }

  return { toggle, stop, state, playing };
}
