"use client";

import type { ProviderName } from "@/lib/api";
import { useProvider } from "@/lib/provider";

/** "I listen on: Spotify | Apple Music". Remembered in this browser. */
export default function ProviderSwitch({ className = "" }: { className?: string }) {
  const { name, all, set } = useProvider();
  return (
    <div className={`flex items-center gap-3 ${className}`}>
      <span className="label text-dim">I listen on</span>
      <div role="radiogroup" aria-label="Your music service" className="inline-flex rounded-md border border-line bg-panel p-0.5">
        {all.map((p) => (
          <button
            key={p.name}
            role="radio"
            aria-checked={name === p.name}
            onClick={() => set(p.name as ProviderName)}
            className={`btn-label rounded px-3 py-1.5 transition ${
              name === p.name ? "bg-wine text-text" : "text-muted hover:text-text"
            }`}
          >
            {p.label}
          </button>
        ))}
      </div>
    </div>
  );
}
