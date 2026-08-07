/** Office wall-clock timezone — set from GET /api/config (backend Settings). */
let officeTimezone = "Europe/Moscow";

let timeFmt = new Intl.DateTimeFormat("en-GB", {
  timeZone: officeTimezone,
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

let dateFmt = new Intl.DateTimeFormat("en-CA", {
  timeZone: officeTimezone,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

let partsFmt = new Intl.DateTimeFormat("en-GB", {
  timeZone: officeTimezone,
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  hourCycle: "h23",
});

function rebuildFormatters(tz: string) {
  timeFmt = new Intl.DateTimeFormat("en-GB", {
    timeZone: tz,
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  dateFmt = new Intl.DateTimeFormat("en-CA", {
    timeZone: tz,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  partsFmt = new Intl.DateTimeFormat("en-GB", {
    timeZone: tz,
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    hourCycle: "h23",
  });
}

/** Apply timezone from backend config (session-scoped). */
export function setOfficeTimezone(tz: string) {
  if (!tz || tz === officeTimezone) return;
  officeTimezone = tz;
  rebuildFormatters(tz);
}

/** Short label for UI (МСК for Europe/Moscow, else IANA id). */
export function officeZoneLabel(tz?: string): string {
  const zone = tz || officeTimezone;
  if (zone === "Europe/Moscow") return "МСК";
  return zone;
}

function officeHourMinute(isoOrDate: string | Date): { hour: number; minute: number } {
  const d = typeof isoOrDate === "string" ? new Date(isoOrDate) : isoOrDate;
  const parts = partsFmt.formatToParts(d);
  const hour = Number(parts.find((p) => p.type === "hour")?.value ?? "0");
  const minute = Number(parts.find((p) => p.type === "minute")?.value ?? "0");
  return { hour, minute };
}

/** Calendar date YYYY-MM-DD in office timezone. */
export function officeTodayISO(now = new Date()): string {
  return dateFmt.format(now);
}

export function addOfficeDaysISO(base: string, days: number): string {
  const [y, m, d] = base.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d + days));
  const yy = dt.getUTCFullYear();
  const mm = String(dt.getUTCMonth() + 1).padStart(2, "0");
  const dd = String(dt.getUTCDate()).padStart(2, "0");
  return `${yy}-${mm}-${dd}`;
}

/**
 * Format an instant for UI in office timezone.
 * Exclusive end at local midnight → "24:00".
 */
export function formatOfficeTime(iso: string, role: "start" | "end" = "start"): string {
  const { hour, minute } = officeHourMinute(iso);
  if (role === "end" && hour === 0 && minute === 0) {
    return "24:00";
  }
  // en-GB can yield "24:00" for midnight in some engines — normalize
  const label = timeFmt.format(new Date(iso));
  if (label === "24:00" && role === "start") {
    return "00:00";
  }
  return label;
}

/** Long datetime for booking lists (office zone). */
export function formatOfficeDateTime(iso: string): string {
  return new Intl.DateTimeFormat("ru-RU", {
    timeZone: officeTimezone,
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(iso));
}

/**
 * Convert office wall-clock (YYYY-MM-DD + HH:MM) to a UTC ISO instant.
 * Iteratively corrects for the configured office timezone offset.
 */
export function officeWallClockToUTCISO(dateISO: string, timeHHMM: string): string | null {
  const [y, m, d] = dateISO.split("-").map(Number);
  const [hh, mm] = timeHHMM.split(":").map(Number);
  if ([y, m, d, hh, mm].some((n) => Number.isNaN(n))) return null;

  let guess = Date.UTC(y, m - 1, d, hh, mm, 0);
  for (let i = 0; i < 4; i++) {
    const asDate = new Date(guess);
    const gotDate = dateFmt.format(asDate);
    const { hour, minute } = officeHourMinute(asDate);
    if (gotDate === dateISO && hour === hh && minute === mm) {
      return asDate.toISOString();
    }
    const [gy, gm, gd] = gotDate.split("-").map(Number);
    const gotMs = Date.UTC(gy, gm - 1, gd, hour, minute, 0);
    const wantMs = Date.UTC(y, m - 1, d, hh, mm, 0);
    guess += wantMs - gotMs;
  }
  return new Date(guess).toISOString();
}
