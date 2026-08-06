/** Office wall-clock timezone. Moscow has no DST (fixed UTC+3). */
export const OFFICE_TIMEZONE = "Europe/Moscow";

const timeFmt = new Intl.DateTimeFormat("en-GB", {
  timeZone: OFFICE_TIMEZONE,
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

const dateFmt = new Intl.DateTimeFormat("en-CA", {
  timeZone: OFFICE_TIMEZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

const partsFmt = new Intl.DateTimeFormat("en-GB", {
  timeZone: OFFICE_TIMEZONE,
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  hourCycle: "h23",
});

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
    timeZone: OFFICE_TIMEZONE,
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(iso));
}
