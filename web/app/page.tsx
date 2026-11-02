import { Suspense } from "react";
import SeedScreen from "@/components/seed-client";

// SeedScreen restores the saved draft from localStorage, so it renders in the browser only
// (inside Suspense because it reads ?fresh= with useSearchParams).
export default function Home() {
  return (
    <Suspense fallback={<p className="py-12 label text-dim">Loading…</p>}>
      <SeedScreen />
    </Suspense>
  );
}
