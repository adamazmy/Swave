"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "./api";

/** Album cover URL per track uri (undefined while loading, null when there is none), fetched in
 *  the background for uris not asked yet, so lists render before their covers. */
export function useCovers(uris: string[]): Record<string, string | null> {
  const [covers, setCovers] = useState<Record<string, string | null>>({});
  const asked = useRef(new Set<string>());
  const key = uris.join(",");
  useEffect(() => {
    const todo = key.split(",").filter((u) => u && !asked.current.has(u));
    todo.forEach((u) => asked.current.add(u));
    for (let i = 0; i < todo.length; i += 100) {
      const batch = todo.slice(i, i + 100);
      api.covers(batch)
        .then((got) => setCovers((c) => ({ ...c, ...got })))
        .catch(() => batch.forEach((u) => asked.current.delete(u))); // retried on the next change
    }
  }, [key]);
  return covers;
}
