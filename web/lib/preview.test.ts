import { describe, expect, it } from "vitest";
import { isExpired } from "./use-preview";

describe("isExpired", () => {
  const now = 1_800_000_000_000;
  it("reads the exp= stamp of signed preview URLs", () => {
    expect(isExpired("https://cdn.example/p.mp3?hdnea=exp=1700000000~acl=/*~hmac=ab", now)).toBe(true);
    expect(isExpired("https://cdn.example/p.mp3?hdnea=exp=1900000000~acl=/*~hmac=ab", now)).toBe(false);
  });
  it("treats URLs without a stamp as not expired", () => {
    expect(isExpired("https://audio.example/preview.m4a", now)).toBe(false);
  });
});
