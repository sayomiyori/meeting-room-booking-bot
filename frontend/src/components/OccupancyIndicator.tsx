import { motion } from "motion/react";
import type { SlotStatus } from "@/lib/api";
import { cn } from "@/lib/utils";

const colorMap: Record<SlotStatus, string> = {
  free: "bg-free",
  soon_free: "bg-soon",
  busy: "bg-busy",
};

const labelMap: Record<SlotStatus, string> = {
  free: "Свободно",
  soon_free: "Скоро свободно",
  busy: "Занято",
};

type Props = {
  status: SlotStatus;
  className?: string;
};

export function OccupancyIndicator({ status, className }: Props) {
  return (
    <div className={cn("flex items-center gap-2", className)}>
      <div className="h-3 w-3 [perspective:40px]">
        <motion.div
          key={status}
          initial={{ rotateX: 90, opacity: 0.4 }}
          animate={{ rotateX: 0, opacity: 1 }}
          transition={{ type: "spring", stiffness: 320, damping: 22 }}
          className={cn("h-3 w-3 rounded-full", colorMap[status])}
          style={{ transformStyle: "preserve-3d" }}
        />
      </div>
      <span className="text-sm font-medium text-muted">{labelMap[status]}</span>
    </div>
  );
}
