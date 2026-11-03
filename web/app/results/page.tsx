import { Suspense } from "react";
import ResultsScreen from "@/components/results-client";
import { Loading } from "@/components/night";

// ResultsScreen reads ?seeds= with useSearchParams and restores tab state from sessionStorage,
// so it renders in the browser only (inside Suspense).
export default function ResultsPage() {
  return (
    <Suspense fallback={<Loading />}>
      <ResultsScreen />
    </Suspense>
  );
}
