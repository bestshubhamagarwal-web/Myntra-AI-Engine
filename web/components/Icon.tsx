"use client";

import { cn } from "@/lib/cn";

export function Icon({
  name,
  className,
  filled,
}: {
  name: string;
  className?: string;
  filled?: boolean;
}) {
  return (
    <span
      className={cn("material-symbols-outlined", className)}
      style={filled ? { fontVariationSettings: "'FILL' 1, 'wght' 400" } : undefined}
      aria-hidden
    >
      {name}
    </span>
  );
}
