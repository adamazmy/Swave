import { Suspense } from "react";
import ResultsScreen from "@/components/results-client";

// ResultsScreen reads ?seeds= with useSearchParams and restores tab state from sessionStorage,
// so it renders in the browser only (inside Suspense).
export default function ResultsPage() {
  return (
    <Suspense fallback={<p className="py-12 label text-dim">Loading…</p>}>
      <ResultsScreen />
    </Suspense>
  );
}
