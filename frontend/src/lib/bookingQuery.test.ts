import { describe, expect, it } from "vitest";
import { parseBookingQuery } from "./bookingQuery";

describe("parseBookingQuery", () => {
  it("parses valid query params", () => {
    const result = parseBookingQuery("?room=3&date=2026-08-10&start=15:00&duration=60");
    expect(result).toEqual({
      roomId: 3,
      date: "2026-08-10",
      start: "15:00",
      duration: 60,
    });
  });

  it("normalizes single-digit hour", () => {
    const result = parseBookingQuery("room=1&date=2026-08-10&start=9:30&duration=30");
    expect(result?.start).toBe("09:30");
  });

  it("returns null when params missing", () => {
    expect(parseBookingQuery("")).toBeNull();
    expect(parseBookingQuery("?room=1&date=2026-08-10")).toBeNull();
  });

  it("returns null for invalid values", () => {
    expect(parseBookingQuery("?room=abc&date=2026-08-10&start=15:00&duration=60")).toBeNull();
    expect(parseBookingQuery("?room=1&date=10-08-2026&start=15:00&duration=60")).toBeNull();
    expect(parseBookingQuery("?room=1&date=2026-08-10&start=25:00&duration=60")).toBeNull();
    expect(parseBookingQuery("?room=1&date=2026-08-10&start=15:00&duration=-5")).toBeNull();
  });
});
