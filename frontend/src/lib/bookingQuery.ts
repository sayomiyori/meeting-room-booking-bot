/**
 * Parse Mini App deep-link query params from /book (Tier 3).
 * Params: room (id), date (YYYY-MM-DD), start (HH:MM), duration (minutes).
 */

export type BookingQueryParams = {
  roomId: number;
  date: string;
  start: string;
  duration: number;
};

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const TIME_RE = /^\d{1,2}:\d{2}$/;

export function parseBookingQuery(search: string): BookingQueryParams | null {
  const params = new URLSearchParams(
    search.startsWith("?") ? search.slice(1) : search,
  );
  const roomRaw = params.get("room");
  const date = params.get("date");
  const start = params.get("start");
  const durationRaw = params.get("duration");

  if (!roomRaw || !date || !start || !durationRaw) {
    return null;
  }

  const roomId = Number(roomRaw);
  const duration = Number(durationRaw);
  if (!Number.isInteger(roomId) || roomId <= 0) return null;
  if (!DATE_RE.test(date)) return null;
  if (!TIME_RE.test(start)) return null;
  if (!Number.isInteger(duration) || duration <= 0) return null;

  const [hh, mm] = start.split(":").map(Number);
  if (hh < 0 || hh > 23 || mm < 0 || mm > 59) return null;

  const normalizedStart = `${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}`;
  return { roomId, date, start: normalizedStart, duration };
}
