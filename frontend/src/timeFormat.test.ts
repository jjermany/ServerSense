import { describe, expect, it } from "vitest";
import { formatDate, formatDateTime, localHour } from "./timeFormat";

describe("configured timezone formatting", () => {
  it("uses the selected timezone even when it changes the calendar day", () => {
    const timestamp = "2026-09-02T02:00:00Z";

    expect(formatDate(timestamp, "America/Chicago")).toContain("Sep 1, 2026");
    expect(formatDate("2026-09-02T02:00:00", "America/Chicago")).toContain(
      "Sep 1, 2026",
    );
    expect(formatDate(timestamp, "UTC")).toContain("Sep 2, 2026");
    expect(formatDateTime(timestamp, "America/Chicago")).toContain("CDT");
    expect(localHour(new Date(timestamp), "America/Chicago")).toBe(21);
  });
});
