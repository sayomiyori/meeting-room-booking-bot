import type { ButtonHTMLAttributes } from "react";
import { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  api,
  hasTelegramContext,
  initTelegramChrome,
  type Booking,
  type Room,
  type SlotPublic,
  type SlotStatus,
} from "@/lib/api";
import { OccupancyIndicator } from "@/components/OccupancyIndicator";
import { cn } from "@/lib/utils";

type Step = "rooms" | "date" | "slots" | "confirm" | "mine";

function todayISO() {
  return new Date().toISOString().slice(0, 10);
}

function addDaysISO(base: string, days: number) {
  const d = new Date(`${base}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

function roomStatusFromSlots(slots: SlotPublic[]): SlotStatus {
  const now = Date.now();
  const busy = slots.find((s) => {
    if (s.status === "free") return false;
    const start = new Date(s.start).getTime();
    const end = new Date(s.end).getTime();
    return start <= now && now < end;
  });
  if (!busy) return "free";
  return busy.status;
}

function formatTime(iso: string) {
  return new Date(iso).toLocaleTimeString("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
  });
}

function GhostButton({
  children,
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      className={cn(
        "rounded-[10px] border border-border bg-transparent px-4 py-2.5 text-sm font-medium text-foreground",
        "disabled:cursor-not-allowed disabled:opacity-40",
        "active:scale-[0.98]",
        className,
      )}
      {...props}
    >
      {children}
    </button>
  );
}

export default function App() {
  const [ready, setReady] = useState(false);
  const [allowed, setAllowed] = useState(false);
  const [step, setStep] = useState<Step>("rooms");
  const [rooms, setRooms] = useState<Room[]>([]);
  const [statuses, setStatuses] = useState<Record<number, SlotStatus>>({});
  const [selectedRoom, setSelectedRoom] = useState<Room | null>(null);
  const [selectedDate, setSelectedDate] = useState(todayISO());
  const [slots, setSlots] = useState<SlotPublic[]>([]);
  const [pickStart, setPickStart] = useState<string | null>(null);
  const [pickEnd, setPickEnd] = useState<string | null>(null);
  const [mine, setMine] = useState<Booking[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const dates = useMemo(
    () => Array.from({ length: 14 }, (_, i) => addDaysISO(todayISO(), i)),
    [],
  );

  useEffect(() => {
    initTelegramChrome();
    const ok = hasTelegramContext();
    setAllowed(ok);
    setReady(true);
    if (!ok) return;

    (async () => {
      try {
        const list = await api.rooms();
        setRooms(list);
        const day = todayISO();
        const entries = await Promise.all(
          list.map(async (room) => {
            const res = await api.slots(room.id, day);
            return [room.id, roomStatusFromSlots(res.slots)] as const;
          }),
        );
        setStatuses(Object.fromEntries(entries));
      } catch (e) {
        setError(e instanceof Error ? e.message : "Не удалось загрузить комнаты");
      }
    })();
  }, []);

  async function openDate(room: Room) {
    setSelectedRoom(room);
    setStep("date");
    setError(null);
  }

  async function loadSlots(date: string) {
    if (!selectedRoom) return;
    setSelectedDate(date);
    setBusy(true);
    setError(null);
    try {
      const res = await api.slots(selectedRoom.id, date);
      setSlots(res.slots);
      setPickStart(null);
      setPickEnd(null);
      setStep("slots");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка слотов");
    } finally {
      setBusy(false);
    }
  }

  function selectSlot(slot: SlotPublic) {
    if (slot.status !== "free") return;
    setPickStart(slot.start);
    // default 1 hour or until slot end
    const start = new Date(slot.start).getTime();
    const endCap = new Date(slot.end).getTime();
    const oneHour = start + 60 * 60 * 1000;
    setPickEnd(new Date(Math.min(oneHour, endCap)).toISOString());
    setStep("confirm");
  }

  async function confirmBooking() {
    if (!selectedRoom || !pickStart || !pickEnd) return;
    setBusy(true);
    setError(null);
    try {
      await api.createBooking(selectedRoom.id, pickStart, pickEnd);
      window.Telegram?.WebApp?.HapticFeedback?.impactOccurred("medium");
      await openMine();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось забронировать");
    } finally {
      setBusy(false);
    }
  }

  async function openMine() {
    setBusy(true);
    setError(null);
    try {
      setMine(await api.myBookings());
      setStep("mine");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось загрузить брони");
    } finally {
      setBusy(false);
    }
  }

  async function cancel(id: number) {
    setBusy(true);
    setError(null);
    try {
      await api.cancelBooking(id);
      setMine(await api.myBookings());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось отменить");
    } finally {
      setBusy(false);
    }
  }

  if (!ready) {
    return <div className="min-h-dvh bg-canvas" />;
  }

  if (!allowed) {
    return (
      <main className="mx-auto flex min-h-dvh max-w-md flex-col justify-center px-5">
        <div className="rounded-[12px] bg-panel p-6">
          <h1 className="text-lg font-semibold">Откройте через бота</h1>
          <p className="mt-2 text-sm text-muted">
            Mini App нужно запускать кнопкой «Забронировать» в Telegram-боте — так
            передаётся безопасная сессия.
          </p>
        </div>
      </main>
    );
  }

  return (
    <main className="mx-auto min-h-dvh max-w-md px-4 pb-10 pt-5">
      <header className="mb-5 flex items-center justify-between">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-muted">
            Переговорные
          </p>
          <h1 className="text-xl font-semibold">Бронирование</h1>
        </div>
        <GhostButton onClick={() => (step === "mine" ? setStep("rooms") : openMine())}>
          {step === "mine" ? "Комнаты" : "Мои брони"}
        </GhostButton>
      </header>

      {error && (
        <div className="mb-4 rounded-[10px] border border-busy/40 bg-panel px-3 py-2 text-sm text-busy">
          {error}
        </div>
      )}

      <AnimatePresence mode="wait">
        <motion.div
          key={step}
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -6 }}
          transition={{ duration: 0.2 }}
        >
          {step === "rooms" && (
            <div className="flex flex-col gap-3">
              {rooms.map((room) => (
                <button
                  key={room.id}
                  type="button"
                  onClick={() => openDate(room)}
                  className="overflow-hidden rounded-[12px] bg-panel text-left"
                >
                  <img
                    src={room.photo_url}
                    alt=""
                    className="h-36 w-full object-cover"
                  />
                  <div className="space-y-2 p-3">
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <h2 className="text-base font-semibold">{room.name}</h2>
                        <p className="text-sm text-muted">до {room.capacity} чел.</p>
                      </div>
                      <OccupancyIndicator status={statuses[room.id] ?? "free"} />
                    </div>
                    <p className="text-sm text-muted">{room.description}</p>
                  </div>
                </button>
              ))}
            </div>
          )}

          {step === "date" && selectedRoom && (
            <div className="rounded-[12px] bg-panel p-4">
              <GhostButton className="mb-4" onClick={() => setStep("rooms")}>
                Назад
              </GhostButton>
              <h2 className="mb-1 text-base font-semibold">{selectedRoom.name}</h2>
              <p className="mb-4 text-sm text-muted">Выберите дату</p>
              <div className="grid grid-cols-7 gap-2">
                {dates.map((d) => {
                  const day = Number(d.slice(8, 10));
                  const selected = d === selectedDate;
                  return (
                    <button
                      key={d}
                      type="button"
                      onClick={() => loadSlots(d)}
                      className={cn(
                        "flex h-10 w-10 items-center justify-center rounded-full text-sm font-medium",
                        selected
                          ? "bg-accent text-white"
                          : "text-foreground hover:bg-border/60",
                      )}
                    >
                      {day}
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {step === "slots" && selectedRoom && (
            <div className="space-y-3">
              <GhostButton onClick={() => setStep("date")}>Назад</GhostButton>
              <div className="rounded-[12px] bg-panel p-4">
                <h2 className="font-semibold">
                  {selectedRoom.name} · {selectedDate}
                </h2>
                <p className="mt-1 text-sm text-muted">Свободные интервалы (UTC)</p>
              </div>
              <div className="flex flex-col gap-2">
                {slots.map((slot) => (
                  <button
                    key={`${slot.start}-${slot.end}`}
                    type="button"
                    disabled={slot.status !== "free" || busy}
                    onClick={() => selectSlot(slot)}
                    className="flex items-center justify-between rounded-[12px] bg-panel px-3 py-3 text-left disabled:opacity-50"
                  >
                    <span className="text-sm font-medium">
                      {formatTime(slot.start)} — {formatTime(slot.end)}
                    </span>
                    <OccupancyIndicator status={slot.status} />
                  </button>
                ))}
              </div>
            </div>
          )}

          {step === "confirm" && selectedRoom && pickStart && pickEnd && (
            <div className="rounded-[12px] bg-panel p-4">
              <GhostButton className="mb-4" onClick={() => setStep("slots")}>
                Назад
              </GhostButton>
              <h2 className="text-base font-semibold">Подтверждение</h2>
              <p className="mt-2 text-sm text-muted">
                {selectedRoom.name}
                <br />
                {formatTime(pickStart)} — {formatTime(pickEnd)} UTC · {selectedDate}
              </p>
              <div className="mt-5 flex gap-2">
                <GhostButton className="flex-1" disabled={busy} onClick={confirmBooking}>
                  {busy ? "Бронируем…" : "Забронировать"}
                </GhostButton>
              </div>
            </div>
          )}

          {step === "mine" && (
            <div className="space-y-3">
              {mine.length === 0 && (
                <div className="rounded-[12px] bg-panel p-4 text-sm text-muted">
                  Активных броней нет
                </div>
              )}
              {mine.map((b) => (
                <div key={b.id} className="rounded-[12px] bg-panel p-4">
                  <h3 className="font-semibold">{b.room_name ?? `Комната #${b.room_id}`}</h3>
                  <p className="mt-1 text-sm text-muted">
                    {new Date(b.start).toLocaleString("ru-RU", { timeZone: "UTC" })} —{" "}
                    {formatTime(b.end)} UTC
                  </p>
                  <GhostButton className="mt-3" disabled={busy} onClick={() => cancel(b.id)}>
                    Отменить
                  </GhostButton>
                </div>
              ))}
            </div>
          )}
        </motion.div>
      </AnimatePresence>
    </main>
  );
}
