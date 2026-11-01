import { describe, expect, it } from "vitest";
import {
  addSeed,
  mergeKept,
  parseSeeds,
  refreshKept,
  replaceAt,
  sameSet,
  sliderToAlpha,
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

describe("sliderToAlpha", () => {
  it("clamps to 0..1", () => {
    expect(sliderToAlpha(-1)).toBe(0);
    expect(sliderToAlpha(0.4)).toBe(0.4);
    expect(sliderToAlpha(3)).toBe(1);
  });
});
