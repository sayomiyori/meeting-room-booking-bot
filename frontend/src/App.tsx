import type { ButtonHTMLAttributes, ReactNode } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  api,
  hasTelegramContext,
  initTelegramChrome,
  type Booking,
  type BookingConfig,
  type Room,
  type SlotPublic,
  type SlotStatus,
} from "@/lib/api";
import { OccupancyIndicator } from "@/components/OccupancyIndicator";
import { cn } from "@/lib/utils";
import {
  addOfficeDaysISO,
  formatOfficeDateTime,
  formatOfficeTime,
  officeTodayISO,
  officeZoneLabel,
  setOfficeTimezone,
} from "@/lib/timezone";

type Step = "rooms" | "date" | "slots" | "pick" | "confirm" | "mine";

function todayISO() {
  return officeTodayISO();
}

function addDaysISO(base: string, days: number) {
  return addOfficeDaysISO(base, days);
}

function durationLabel(minutes: number): string {
  if (minutes < 60) return `${minutes}м`;
  const hours = minutes / 60;
  return Number.isInteger(hours) ? `${hours}ч` : `${hours}ч`;
}

function buildDurationOptions(stepMinutes: number, maxMinutes: number) {
  const options: { minutes: number; label: string }[] = [];
  for (let m = stepMinutes; m <= maxMinutes; m += stepMinutes) {
    options.push({ minutes: m, label: durationLabel(m) });
  }
  return options;
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

/**
 * Start options every slot_step_minutes in [intervalStart, intervalEnd).
 * Stored as UTC ISO for API; UI labels use formatOfficeTime (office TZ).
 */
function buildStartOptions(
  intervalStartIso: string,
  intervalEndIso: string,
  stepMinutes: number,
): string[] {
  const startMs = new Date(intervalStartIso).getTime();
  const endMs = new Date(intervalEndIso).getTime();
  const step = stepMinutes * 60 * 1000;
  const options: string[] = [];
  for (let t = startMs; t + step <= endMs; t += step) {
    options.push(new Date(t).toISOString());
  }
  return options;
}

function durationFits(
  startIso: string,
  minutes: number,
  intervalEndIso: string,
  maxDurationMinutes: number,
): boolean {
  if (minutes > maxDurationMinutes) return false;
  const endMs = new Date(startIso).getTime() + minutes * 60 * 1000;
  return endMs <= new Date(intervalEndIso).getTime();
}

function ChoiceChip({
  children,
  active,
  disabled,
  onClick,
}: {
  children: ReactNode;
  active?: boolean;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "rounded-[10px] px-3 py-2 text-sm font-medium",
        "disabled:cursor-not-allowed disabled:opacity-40",
        "active:scale-[0.98]",
        active
          ? "border border-accent bg-accent text-white"
          : "border border-border bg-transparent text-foreground",
      )}
    >
      {children}
    </button>
  );
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
  const [config, setConfig] = useState<BookingConfig | null>(null);
  const [step, setStep] = useState<Step>("rooms");
  const [rooms, setRooms] = useState<Room[]>([]);
  const [statuses, setStatuses] = useState<Record<number, SlotStatus>>({});
  const [selectedRoom, setSelectedRoom] = useState<Room | null>(null);
  const [selectedDate, setSelectedDate] = useState(todayISO());
  const [slots, setSlots] = useState<SlotPublic[]>([]);
  const [freeInterval, setFreeInterval] = useState<SlotPublic | null>(null);
  const [pickStartOption, setPickStartOption] = useState<string | null>(null);
  const [pickDuration, setPickDuration] = useState<number | null>(null);
  const [pickStart, setPickStart] = useState<string | null>(null);
  const [pickEnd, setPickEnd] = useState<string | null>(null);
  const [mine, setMine] = useState<Booking[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const zoneLabel = officeZoneLabel(config?.office_timezone);
  const durationOptions = useMemo(() => {
    if (!config) return [];
    return buildDurationOptions(config.slot_step_minutes, config.max_duration_minutes);
  }, [config]);

  const stepRef = useRef(step);
  const roomRef = useRef(selectedRoom);
  const dateRef = useRef(selectedDate);
  stepRef.current = step;
  roomRef.current = selectedRoom;
  dateRef.current = selectedDate;

  const dates = useMemo(
    () => Array.from({ length: 14 }, (_, i) => addDaysISO(todayISO(), i)),
    [],
  );

  const loadRooms = useCallback(async () => {
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
  }, []);

  const loadSlotsFor = useCallback(async (room: Room, date: string) => {
    const res = await api.slots(room.id, date);
    setSlots(res.slots);
    setSelectedDate(date);
  }, []);

  const loadMine = useCallback(async () => {
    setMine(await api.myBookings());
  }, []);

  const refetchCurrentScreen = useCallback(async () => {
    if (!hasTelegramContext()) return;
    try {
      const current = stepRef.current;
      if (current === "rooms" || current === "date") {
        await loadRooms();
      } else if (
        (current === "slots" || current === "pick" || current === "confirm") &&
        roomRef.current
      ) {
        await loadSlotsFor(roomRef.current, dateRef.current);
        await loadRooms();
      } else if (current === "mine") {
        await loadMine();
        await loadRooms();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось обновить данные");
    }
  }, [loadMine, loadRooms, loadSlotsFor]);

  useEffect(() => {
    initTelegramChrome();
    const ok = hasTelegramContext();
    setAllowed(ok);
    if (!ok) {
      setReady(true);
      return;
    }

    void (async () => {
      try {
        const cfg = await api.config();
        setOfficeTimezone(cfg.office_timezone);
        setConfig(cfg);
        setSelectedDate(officeTodayISO());
        await loadRooms();
      } catch (e) {
        setError(e instanceof Error ? e.message : "Не удалось загрузить данные");
      } finally {
        setReady(true);
      }
    })();
  }, [loadRooms]);

  // Refetch when returning from Telegram chat / another app (bot cancel, etc.)
  useEffect(() => {
    if (!allowed) return;

    const onVisible = () => {
      if (document.visibilityState === "visible") {
        void refetchCurrentScreen();
      }
    };
    document.addEventListener("visibilitychange", onVisible);

    const wa = window.Telegram?.WebApp;
    const onViewport = () => {
      void refetchCurrentScreen();
    };
    wa?.onEvent?.("viewportChanged", onViewport);

    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      wa?.offEvent?.("viewportChanged", onViewport);
    };
  }, [allowed, refetchCurrentScreen]);

  async function openDate(room: Room) {
    setSelectedRoom(room);
    setStep("date");
    setError(null);
  }

  async function loadSlots(date: string) {
    if (!selectedRoom) return;
    setBusy(true);
    setError(null);
    try {
      await loadSlotsFor(selectedRoom, date);
      setPickStart(null);
      setPickEnd(null);
      setFreeInterval(null);
      setPickStartOption(null);
      setPickDuration(null);
      setStep("slots");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка слотов");
    } finally {
      setBusy(false);
    }
  }

  function selectSlot(slot: SlotPublic) {
    if (slot.status !== "free" || !config) return;
    const starts = buildStartOptions(slot.start, slot.end, config.slot_step_minutes);
    if (starts.length === 0) {
      setError("В этом интервале нет доступного времени для бронирования");
      return;
    }
    setFreeInterval(slot);
    setPickStartOption(starts[0]);
    setPickDuration(null);
    setPickStart(null);
    setPickEnd(null);
    setError(null);
    setStep("pick");
  }

  function goToConfirm() {
    if (!config || !freeInterval || !pickStartOption || pickDuration == null) return;
    if (
      !durationFits(
        pickStartOption,
        pickDuration,
        freeInterval.end,
        config.max_duration_minutes,
      )
    ) {
      return;
    }
    const end = new Date(
      new Date(pickStartOption).getTime() + pickDuration * 60 * 1000,
    ).toISOString();
    setPickStart(pickStartOption);
    setPickEnd(end);
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
      await loadMine();
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
      await loadMine();
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
                  <div className="h-[168px] w-full overflow-hidden rounded-t-[12px]">
                    <img
                      src={room.photo_url}
                      alt=""
                      className="h-full w-full object-cover object-center"
                    />
                  </div>
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
                <p className="mt-1 text-sm text-muted">Свободные интервалы ({zoneLabel})</p>
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
                      {formatOfficeTime(slot.start, "start")} — {formatOfficeTime(slot.end, "end")}
                    </span>
                    <OccupancyIndicator status={slot.status} />
                  </button>
                ))}
              </div>
            </div>
          )}

          {step === "pick" && selectedRoom && freeInterval && config && (
            <div className="space-y-3">
              <GhostButton onClick={() => setStep("slots")}>Назад</GhostButton>
              <div className="rounded-[12px] bg-panel p-4">
                <h2 className="text-base font-semibold">
                  {selectedRoom.name} · {selectedDate}
                </h2>
                <p className="mt-1 text-sm text-muted">
                  Интервал{" "}
                  {formatOfficeTime(freeInterval.start, "start")} —{" "}
                  {formatOfficeTime(freeInterval.end, "end")} {zoneLabel}
                </p>
              </div>

              <div className="rounded-[12px] bg-panel p-4">
                <p className="mb-3 text-sm font-medium">Начало</p>
                <div className="flex flex-wrap gap-2">
                  {buildStartOptions(
                    freeInterval.start,
                    freeInterval.end,
                    config.slot_step_minutes,
                  ).map((iso) => (
                    <ChoiceChip
                      key={iso}
                      active={pickStartOption === iso}
                      onClick={() => {
                        setPickStartOption(iso);
                        if (
                          pickDuration != null &&
                          !durationFits(
                            iso,
                            pickDuration,
                            freeInterval.end,
                            config.max_duration_minutes,
                          )
                        ) {
                          setPickDuration(null);
                        }
                      }}
                    >
                      {formatOfficeTime(iso, "start")}
                    </ChoiceChip>
                  ))}
                </div>
              </div>

              <div className="rounded-[12px] bg-panel p-4">
                <p className="mb-3 text-sm font-medium">Длительность</p>
                <div className="flex flex-wrap gap-2">
                  {durationOptions.map((opt) => {
                    const ok =
                      pickStartOption != null &&
                      durationFits(
                        pickStartOption,
                        opt.minutes,
                        freeInterval.end,
                        config.max_duration_minutes,
                      );
                    return (
                      <ChoiceChip
                        key={opt.minutes}
                        active={pickDuration === opt.minutes}
                        disabled={!ok}
                        onClick={() => setPickDuration(opt.minutes)}
                      >
                        {opt.label}
                      </ChoiceChip>
                    );
                  })}
                </div>
              </div>

              <GhostButton
                className="w-full"
                disabled={
                  !pickStartOption ||
                  pickDuration == null ||
                  !durationFits(
                    pickStartOption,
                    pickDuration,
                    freeInterval.end,
                    config.max_duration_minutes,
                  )
                }
                onClick={goToConfirm}
              >
                Далее
              </GhostButton>
            </div>
          )}

          {step === "confirm" && selectedRoom && pickStart && pickEnd && (
            <div className="rounded-[12px] bg-panel p-4">
              <GhostButton className="mb-4" onClick={() => setStep("pick")}>
                Назад
              </GhostButton>
              <h2 className="text-base font-semibold">Подтверждение</h2>
              <p className="mt-2 text-sm text-muted">
                {selectedRoom.name}
                <br />
                {formatOfficeTime(pickStart, "start")} — {formatOfficeTime(pickEnd, "end")}{" "}
                {zoneLabel} · {selectedDate}
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
                    {formatOfficeDateTime(b.start)} — {formatOfficeTime(b.end, "end")}{" "}
                    {zoneLabel}
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
