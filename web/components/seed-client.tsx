"use client";

import dynamic from "next/dynamic";
import { useSearchParams } from "next/navigation";

// Browser-only: SeedScreen restores the saved draft and last vibe from localStorage on its
// first render, which must not be compared against server-rendered HTML.
const SeedScreen = dynamic(() => import("./seed-screen"), {
  ssr: false,
  loading: () => <p className="py-12 label text-dim">Loading…</p>,
});

/**
 * Next.js keeps visited pages alive in a hidden <Activity>, so after "Start over" on the
 * Results page the old Seed screen state would come back. Results navigates to
 * `/?fresh=<id>`; keying on it remounts the screen from the (now cleared) storage.
 */
export default function SeedRoute() {
  const fresh = useSearchParams().get("fresh") ?? "default";
  return <SeedScreen key={fresh} />;
}
