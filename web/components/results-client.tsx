"use client";

import dynamic from "next/dynamic";
import { useSearchParams } from "next/navigation";

// Browser-only: ResultsScreen restores the saved vibe from localStorage on its first render,
// which must not be compared against server-rendered HTML.
const ResultsScreen = dynamic(() => import("./results-screen"), {
  ssr: false,
  loading: () => <p className="py-12 label text-dim">Loading…</p>,
});

/**
 * Next.js (cacheComponents) keeps visited pages alive in a hidden <Activity> instead of
 * unmounting them, so going "New vibe → Find songs" would reuse the old Results state.
 * Every new vibe gets a fresh `run` id in the URL; keying on it remounts the screen, while
 * the screen's own URL updates (slider, more-like-this) keep the same run and the same state.
 */
export default function ResultsRoute() {
  const run = useSearchParams().get("run") ?? "default";
  return <ResultsScreen key={run} />;
}
