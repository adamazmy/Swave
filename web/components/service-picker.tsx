"use client";

import { useId } from "react";
import type { ServiceId, ServiceState } from "@/lib/api";
import { notAllowlistedLine, SERVICES } from "@/lib/services";

const focus = "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-pink";

/**
 * The "I listen on" list: one real <button> per service (min 44 px tall). Buttons, not a radio
 * group, because choosing Spotify starts its login redirect at once: arrow keys moving a radio
 * selection would fire it by accident. aria-pressed marks the current choice; Apple Music stays
 * focusable but aria-disabled, so screen readers still announce it as "coming soon".
 */
export default function ServicePicker({
  state,
  onChoose,
  onDisconnect,
}: {
  state: ServiceState | null;
  onChoose: (id: ServiceId) => void;
  onDisconnect: () => void;
}) {
  const base = useId();
  const sp = state?.spotify;
  return (
    <div>
      <ul className="space-y-2" aria-label="Music services">
        {SERVICES.map((s) => {
          const selected = state?.service === s.id;
          const hintId = `${base}-${s.id}`;
          return (
            <li key={s.id}>
              <button
                type="button"
                aria-pressed={selected}
                aria-disabled={!s.available || undefined}
                aria-describedby={hintId}
                onClick={() => s.available && onChoose(s.id)}
                className={`flex min-h-11 w-full items-center justify-between gap-3 rounded-md border px-4 py-2.5 text-left transition ${focus} ${
                  !s.available
                    ? "cursor-not-allowed border-line/60 text-dim"
                    : selected
                      ? "border-pink bg-wine/40 text-text"
                      : "border-line text-text hover:border-pink/60"
                }`}
              >
                <span>
                  <span className="block text-sm font-semibold">{s.label}</span>
                  <span id={hintId} className="block text-xs text-muted">
                    {s.id === "spotify" && selected && sp?.connected
                      ? `Connected as ${sp.display_name}`
                      : s.id === "spotify" && sp?.blocked
                        ? "Not on this app's Spotify list yet"
                        : s.hint}
                  </span>
                </span>
                <span aria-hidden className={`h-4 w-4 shrink-0 rounded-full border ${selected ? "border-pink bg-pink" : "border-line"}`} />
              </button>
            </li>
          );
        })}
      </ul>
      {state?.service === "spotify" && sp?.connected && (
        <p className="mt-3 flex flex-wrap items-center gap-2 text-sm">
          <span className="text-muted">Connected as {sp.display_name}</span>
          <span aria-hidden className="text-dim">·</span>
          <button type="button" onClick={onDisconnect}
                  className={`min-h-11 rounded-md px-2 text-pink underline hover:text-pink-soft ${focus}`}>
            Disconnect
          </button>
        </p>
      )}
      {sp?.blocked && (
        <p className="mt-3 text-sm text-cream" role="status">{notAllowlistedLine(state?.owner_name ?? "the owner")}</p>
      )}
    </div>
  );
}
