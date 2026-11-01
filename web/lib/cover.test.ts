import { describe, expect, it } from "vitest";
import { hueFromString, wrapTitle } from "./cover";

describe("hueFromString", () => {
  it("is deterministic and in range", () => {
    expect(hueFromString("Road trip")).toBe(hueFromString("Road trip"));
    expect(hueFromString("Road trip")).not.toBe(hueFromString("Late night"));
    for (const s of ["", "a", "Swave · Mr. Brightside"]) {
      const h = hueFromString(s);
      expect(h).toBeGreaterThanOrEqual(0);
      expect(h).toBeLessThan(360);
    }
  });
});

describe("wrapTitle", () => {
  it("wraps on words", () => {
    expect(wrapTitle("Swave Mr Brightside vibes", 12)).toEqual(["Swave Mr", "Brightside", "vibes"]);
  });
  it("caps the number of lines with an ellipsis", () => {
    const lines = wrapTitle("one two three four five six seven eight nine ten", 5, 3);
    expect(lines).toHaveLength(3);
    expect(lines[2].endsWith("…")).toBe(true);
  });
});
