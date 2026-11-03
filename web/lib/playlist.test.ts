import { describe, expect, it } from "vitest";
import {
  addSeed,
  isPlaylistLink,
  shortReason,
  toSongList,
  mergeKept,
  parseSeeds,
  refreshKept,
  replaceAt,
  sameSet,
  clampSlider,
  slug,
  toCsv,
} from "./playlist";

// Synthetic ids only.
const u = (i: number) => `spotify:track:fake${String(i).padStart(18, "0")}`;
const t = (i: number) => ({ track_uri: u(i) });

describe("parseSeeds", () => {
  it("keeps valid unique uris, max 10", () => {
    const many = Array.from({ length: 12 }, (_, i) => u(i)).join(",");
    expect(parseSeeds(many)).toHaveLength(10);
    expect(parseSeeds(`${u(1)},${u(1)}, bad ,${u(2)}`)).toEqual([u(1), u(2)]);
    expect(parseSeeds(null)).toEqual([]);
  });
});

describe("mergeKept", () => {
  it("puts kept first, removes duplicates, fills to size", () => {
    const merged = mergeKept([t(9)], [t(1), t(9), t(2), t(3)], 3);
    expect(merged.map((x) => x.track_uri)).toEqual([u(9), u(1), u(2)]);
  });
  it("shows every kept track even past size", () => {
    expect(mergeKept([t(1), t(2), t(3)], [t(4)], 2)).toHaveLength(3);
  });
});

describe("refreshKept", () => {
  const r = (i: number, reason: string) => ({ track_uri: u(i), reason });
  it("takes fresh reasons, marks seeds, keeps the rest as-is", () => {
    const kept = [r(1, "old"), r(2, "old"), r(3, "old")];
    const fresh = [r(1, "new"), r(9, "other")];
    expect(refreshKept(kept, fresh, [u(2)]).map((k) => k.reason)).toEqual([
      "new",
      "one of your seeds",
      "old",
    ]);
  });
});

describe("replaceAt", () => {
  it("replaces in place or drops the row", () => {
    expect(replaceAt([1, 2, 3], 1, 9)).toEqual([1, 9, 3]);
    expect(replaceAt([1, 2, 3], 1)).toEqual([1, 3]);
  });
});

describe("addSeed", () => {
  it("dedupes and caps", () => {
    const seeds = [u(1)];
    expect(addSeed(seeds, u(1))).toBe(seeds);
    expect(addSeed(seeds, u(2))).toEqual([u(1), u(2)]);
    expect(addSeed([u(1), u(2)], u(3), 2)).toEqual([u(1), u(2)]);
  });
});

describe("toCsv", () => {
  it("has a header and escapes commas and quotes", () => {
    const csv = toCsv([{ name: 'Song, "Live"', artist: "Fake Band", album: "Album", track_uri: u(1) }]);
    expect(csv.split("\r\n")).toEqual([
      "Track name,Artist name,Album,Spotify URI",
      `"Song, ""Live""",Fake Band,Album,${u(1)}`,
      "",
    ]);
  });
});

describe("sameSet / slug", () => {
  it("compares ignoring order", () => {
    expect(sameSet([u(1), u(2)], [u(2), u(1)])).toBe(true);
    expect(sameSet([u(1)], [u(1), u(2)])).toBe(false);
  });
  it("makes safe file names", () => {
    expect(slug("Swave · Mr. Brightside!")).toBe("swave-mr-brightside");
    expect(slug("···")).toBe("playlist");
  });
});

describe("clampSlider", () => {
  it("clamps to 0..1", () => {
    expect(clampSlider(-1)).toBe(0);
    expect(clampSlider(0.4)).toBe(0.4);
    expect(clampSlider(3)).toBe(1);
  });
});

describe("isPlaylistLink", () => {
  it("spots playlist links and URIs, not searches", () => {
    expect(isPlaylistLink("https://open.spotify.com/playlist/abc123?si=x")).toBe(true);
    expect(isPlaylistLink("https://open.spotify.com/intl-fr/playlist/abc123")).toBe(true);
    expect(isPlaylistLink(" spotify:playlist:abc123")).toBe(true);
    expect(isPlaylistLink("https://open.spotify.com/track/abc123")).toBe(false);
    expect(isPlaylistLink("playlist love songs")).toBe(false);
    expect(isPlaylistLink("https://music.apple.com/us/playlist/late-night/pl.u-abc123")).toBe(true);
  });
});

describe("shortReason", () => {
  it("drops 'appears' and a repeated 'with … in'", () => {
    expect(shortReason("appears with your seed in 40 shared playlists · appears with your seed in 3 listening sessions"))
      .toBe("with your seed in 40 shared playlists · 3 listening sessions");
    expect(shortReason("appears with 2 of your 3 seeds in 9 shared playlists · appears with 1 of your 3 seeds in 4 listening sessions"))
      .toBe("with 2 of your 3 seeds in 9 shared playlists · with 1 of your 3 seeds in 4 listening sessions");
    expect(shortReason("appears with your seed in 7 listening sessions")).toBe("with your seed in 7 listening sessions");
    expect(shortReason("")).toBe("");
  });
});

describe("toSongList", () => {
  it("writes one Title - Artist line per song", () => {
    expect(toSongList([{ name: "Static Bloom", artist: "Mock Orchestra" }, { name: "Quiet Engine", artist: "Mock Orchestra" }]))
      .toBe(["Static Bloom - Mock Orchestra", "Quiet Engine - Mock Orchestra"].join("\n"));
    expect(toSongList([])).toBe("");
  });
});
