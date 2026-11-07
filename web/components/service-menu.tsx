"use client";

import { useEffect, useRef, useState } from "react";
import { saveMode, serviceDef, useMusicService } from "@/lib/services";
import ServicePicker from "./service-picker";

const focus = "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-pink";

/**
 * Header control for "I listen on": a summary button ("Spotify · Adam", "Just browsing") that
 * opens the picker in a native <dialog> (focus trap, Escape, screen-reader modal). On a user's
 * first visit (no service saved yet) the dialog opens by itself, once: closing it there counts
 * as "Other / just browsing", changeable here at any time.
 */
export default function ServiceMenu() {
  const { state, choose, disconnect } = useMusicService();
  const dialog = useRef<HTMLDialogElement>(null);
  const [firstRun, setFirstRun] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (state && state.service === null && !dialog.current?.open) {
      setFirstRun(true);
      dialog.current?.showModal();
    }
  }, [state]);

  function close() {
    dialog.current?.close();
  }

  async function pick(id: Parameters<typeof choose>[0]) {
    setError(null);
    try {
      await choose(id); // Spotify not connected yet → the page leaves for Spotify's login here
    } catch {
      setError("Couldn't save your choice. Check your connection and try again.");
      return;
    }
    setFirstRun(false);
    close();
  }

  if (!state) return null;
  const def = serviceDef(state.service);
  const sp = state.spotify;
  const summary =
    state.service === "spotify"
      ? sp.connected ? `Spotify · ${sp.display_name}` : sp.blocked ? "Spotify · not on the list" : "Spotify · Connect"
      : state.service === "other" ? "Just browsing" : "Choose your music service";

  return (
    <>
      <button
        type="button"
        onClick={() => dialog.current?.showModal()}
        aria-haspopup="dialog"
        className={`label flex min-h-11 items-center gap-2 rounded-md px-2 text-muted hover:text-text ${focus}`}
        title={saveMode(state) === "spotify" ? "Saves go straight to Spotify" : "Change your music service"}
      >
        <span className="sr-only">Music service: </span>
        <span className="max-w-40 truncate">{summary}</span>
      </button>
      <dialog
        ref={dialog}
        aria-labelledby="service-title"
        onCancel={(e) => {
          if (firstRun) {
            e.preventDefault();
            void pick("other"); // Escape on the first visit = just browsing (changeable later)
          }
        }}
        className="m-auto w-[min(28rem,calc(100vw-2rem))] rounded-lg border border-line bg-ink p-5 text-text backdrop:bg-ink/70"
      >
        <h2 id="service-title" className="font-display text-lg font-semibold tracking-wide">
          {firstRun ? "Where do you listen?" : "Your music service"}
        </h2>
        <p className="mb-4 mt-1 text-sm text-muted">
          {firstRun
            ? "Pick one so “Save the set” just works. You can change it any time from the top bar."
            : def ? `Currently: ${def.label}.` : "Pick the service you listen on."}
        </p>
        <ServicePicker state={state} onChoose={(id) => void pick(id)} onDisconnect={() => void disconnect()} />
        {error && <p role="alert" className="mt-3 text-sm text-pink">{error}</p>}
        {!firstRun && (
          <div className="mt-4 flex justify-end">
            <button type="button" onClick={close}
                    className={`btn-label min-h-11 rounded-md border border-line px-4 text-text hover:border-pink/50 ${focus}`}>
              Done
            </button>
          </div>
        )}
      </dialog>
    </>
  );
}
