"use client";

import dynamic from "next/dynamic";

// Browser-only: ResultsScreen restores tab state from sessionStorage on its first render,
// which must not be compared against server-rendered HTML.
const ResultsScreen = dynamic(() => import("./results-screen"), {
  ssr: false,
  loading: () => <p className="py-12 text-zinc-500">Loading…</p>,
});

export default ResultsScreen;
