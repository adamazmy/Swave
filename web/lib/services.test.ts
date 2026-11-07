import { beforeEach, describe, expect, it } from "vitest";
import type { ServiceState } from "./api";
import { notAllowlistedLine, prepareReturn, saveMode, SERVICES, takeResume, withoutReturnParams } from "./services";

// sessionStorage for the resume handshake (vitest runs in node)
const store = new Map<string, string>();
beforeEach(() => {
  store.clear();
  (globalThis as { sessionStorage?: unknown }).sessionStorage = {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
  };
});

const state = (over: Partial<ServiceState["spotify"]> & { service?: ServiceState["service"] } = {}): ServiceState => ({
  service: over.service === undefined ? "spotify" : over.service,
  owner_name: "Adam",
  spotify: { configured: true, connected: true, display_name: "Fake User", can_upload_cover: true, blocked: false, ...over },
});

describe("saveMode", () => {
  it("saves to Spotify only when it's the chosen, configured, allowed service", () => {
    expect(saveMode(state())).toBe("spotify");
    expect(saveMode(state({ connected: false }))).toBe("spotify"); // saving logs in first
    expect(saveMode(state({ blocked: true }))).toBe("fallback"); // not on the allowlist
    expect(saveMode(state({ configured: false }))).toBe("fallback");
    expect(saveMode(state({ service: "other" }))).toBe("fallback");
    expect(saveMode(state({ service: null }))).toBe("fallback");
    expect(saveMode(null)).toBe("fallback");
  });
});

describe("resume after the OAuth round trip", () => {
  it("comes back to the same page with the action, once", () => {
    const back = prepareReturn("https://swave.party/results?seeds=a,b&v=0.3&run=r1&spotify=connected", "save", 1000);
    const url = new URL(back);
    expect(url.pathname + "?" + [...url.searchParams.keys()].join(",")).toBe("/results?seeds,v,run,resume");
    expect(url.searchParams.get("seeds")).toBe("a,b"); // the page's own state survives
    expect(takeResume(url.searchParams, 2000)).toBe("save");
    expect(takeResume(url.searchParams, 2000)).toBeNull(); // single use
  });

  it("ignores a stale or foreign resume id", () => {
    const back = new URL(prepareReturn("https://swave.party/results?seeds=a", "save-new", 0));
    expect(takeResume(back.searchParams, 16 * 60 * 1000)).toBeNull(); // > 15 min
    prepareReturn("https://swave.party/results?seeds=a", "add", 0);
    expect(takeResume(new URLSearchParams("resume=someone-elses-id"), 1)).toBeNull();
  });

  it("strips the return parameters for the address bar", () => {
    expect(withoutReturnParams("https://swave.party/results?seeds=a&spotify_error=not_allowlisted&resume=x"))
      .toBe("/results?seeds=a");
  });
});

describe("provider list", () => {
  it("lists Spotify, Apple Music (coming soon) and Other, each saying how it connects", () => {
    expect(SERVICES.map((s) => [s.id, s.available, s.connect])).toEqual([
      ["spotify", true, "redirect"], ["apple", false, "popup"], ["other", true, "none"]]);
    expect(notAllowlistedLine("Adam")).toBe("Ask Adam to add your Spotify account; until then use Copy for Spotify.");
  });
});
