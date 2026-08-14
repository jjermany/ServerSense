import { describe, expect, it } from "vitest";
import { formatBytes } from "./api";

describe("formatBytes", () => {
  it("formats decimal storage units consistently", () => {
    expect(formatBytes(4_070_000_000_000, 2)).toBe("4.07 TB");
    expect(formatBytes(620_000_000_000)).toBe("620.0 GB");
  });
});

