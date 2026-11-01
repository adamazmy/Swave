import { Suspense } from "react";
import ResultsScreen from "@/components/results-screen";

// ResultsScreen reads ?seeds= with useSearchParams, so it renders client-side inside Suspense.
export default function ResultsPage() {
  return (
    <Suspense fallback={<p className="py-12 text-zinc-500">Loading…</p>}>
      <ResultsScreen />
    </Suspense>
  );
}
