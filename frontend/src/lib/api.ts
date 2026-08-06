export type Room = {
  id: number;
  name: string;
  capacity: number;
  photo_url: string;
  description: string;
};

export type SlotStatus = "free" | "busy" | "soon_free";

export type SlotPublic = {
  start: string;
  end: string;
  status: SlotStatus;
};

export type Booking = {
  id: number;
  room_id: number;
  room_name: string | null;
  user_display_name: string;
  start: string;
  end: string;
  canceled: boolean;
  created_at: string;
};

export type BookingConfig = {
  office_timezone: string;
  office_hours_start: number;
  office_hours_end: number;
  min_duration_minutes: number;
  max_duration_minutes: number;
  slot_step_minutes: number;
  soon_free_minutes: number;
};

type TelegramWebApp = {
  initData: string;
  ready: () => void;
  expand: () => void;
  setHeaderColor: (color: string) => void;
  setBackgroundColor: (color: string) => void;
  onEvent?: (eventType: string, callback: () => void) => void;
  offEvent?: (eventType: string, callback: () => void) => void;
  HapticFeedback?: { impactOccurred: (style: string) => void };
};

declare global {
  interface Window {
    Telegram?: { WebApp?: TelegramWebApp };
  }
}

const DEBUG_TG_ID = import.meta.env.VITE_DEBUG_TELEGRAM_ID as string | undefined;

/** Session-scoped cache for GET /api/config. */
let cachedConfig: BookingConfig | null = null;
let configPromise: Promise<BookingConfig> | null = null;

export function getInitData(): string {
  return window.Telegram?.WebApp?.initData ?? "";
}

export function hasTelegramContext(): boolean {
  if (getInitData()) return true;
  // Browser / pytest mock only when Vite exposes debug id (local DEV)
  if (import.meta.env.DEV && DEBUG_TG_ID) return true;
  return false;
}

function authHeaders(): HeadersInit {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  const initData = getInitData();
  if (initData) {
    headers["X-Telegram-Init-Data"] = initData;
  } else if (import.meta.env.DEV && DEBUG_TG_ID) {
    headers["X-Debug-Telegram-Id"] = DEBUG_TG_ID;
  }
  return headers;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { ...authHeaders(), ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = "Ошибка запроса";
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

async function fetchConfig(): Promise<BookingConfig> {
  if (cachedConfig) return cachedConfig;
  if (!configPromise) {
    configPromise = request<BookingConfig>("/api/config").then((cfg) => {
      cachedConfig = cfg;
      return cfg;
    });
  }
  return configPromise;
}

export const api = {
  config: fetchConfig,
  rooms: () => request<Room[]>("/api/rooms"),
  slots: (roomId: number, date: string) =>
    request<{ room_id: number; date: string; slots: SlotPublic[] }>(
      `/api/rooms/${roomId}/slots?date=${date}`,
    ),
  createBooking: (room_id: number, start: string, end: string) =>
    request<Booking>("/api/bookings", {
      method: "POST",
      body: JSON.stringify({ room_id, start, end }),
    }),
  myBookings: () => request<Booking[]>("/api/bookings/my"),
  cancelBooking: (id: number) =>
    request<Booking>(`/api/bookings/${id}/cancel`, { method: "POST" }),
};

export function initTelegramChrome() {
  const wa = window.Telegram?.WebApp;
  if (!wa) return;
  wa.ready();
  wa.expand();
  try {
    wa.setHeaderColor("#16171A");
    wa.setBackgroundColor("#16171A");
  } catch {
    /* older clients */
  }
}
