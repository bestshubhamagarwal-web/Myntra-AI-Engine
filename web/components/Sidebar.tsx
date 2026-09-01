"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { Icon } from "@/components/Icon";
import { cn } from "@/lib/cn";
import { NAV_ITEMS } from "@/lib/constants";
import { relativeTime } from "@/lib/format";
import { withCurrentFilters } from "@/lib/filters";
import type { OverviewResponse, ThemesResponse } from "@/lib/types";

export function Sidebar({
  overview,
  themes,
  search,
  mobileOpen,
  onClose,
}: {
  overview?: OverviewResponse;
  themes?: ThemesResponse;
  search: string;
  mobileOpen: boolean;
  onClose: () => void;
}) {
  const pathname = usePathname();
  const failed = overview?.counts_by_source.filter(
    (s) => s.last_run_status === "failed",
  );

  return (
    <nav
      className={cn(
        "z-50 flex h-full min-h-0 w-sidebar flex-col border-r border-outline-variant bg-surface py-6",
        "max-md:fixed max-md:inset-y-0 max-md:left-0 max-md:h-dvh max-md:transition-transform",
        mobileOpen ? "max-md:translate-x-0" : "max-md:-translate-x-full",
        "md:relative md:row-span-2 md:translate-x-0",
      )}
    >
      <div className="mb-8 flex items-start justify-between gap-3 px-gutter">
        <Link href={withCurrentFilters("/overview", search)} className="block min-w-0" onClick={onClose}>
          <h1 className="font-display text-[20px] font-semibold leading-7 tracking-tight text-on-surface">
            Discovery
          </h1>
          <p className="mt-1 font-label-md text-label-md text-on-surface-variant">
            Wishlist insights
          </p>
        </Link>
        <button
          type="button"
          className="rounded-md p-1 text-on-surface-variant hover:bg-surface-container-high md:hidden"
          onClick={onClose}
          aria-label="Close navigation"
        >
          <Icon name="close" />
        </button>
      </div>
      <div className="mb-6 px-gutter">
        <Link
          href={withCurrentFilters("/copilot", search)}
          onClick={onClose}
          className="flex w-full items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 font-label-md text-label-md text-on-primary hover:bg-primary-container"
        >
          <Icon name="smart_toy" className="text-[18px]" />
          Copilot
        </Link>
      </div>
      <ul className="min-h-0 flex-1 space-y-1 overflow-y-auto">
        {NAV_ITEMS.map((item) => {
          const active = pathname === item.href;
          return (
            <li key={item.href}>
              <Link
                href={withCurrentFilters(item.href, search)}
                onClick={onClose}
                className={
                  active
                    ? "flex items-center gap-3 border-l-4 border-primary bg-secondary-container px-4 py-2 text-on-secondary-container"
                    : "flex items-center gap-3 px-4 py-2 text-on-surface-variant hover:bg-surface-container-high"
                }
              >
                <Icon name={item.icon} filled={active} />
                {item.label}
              </Link>
            </li>
          );
        })}
      </ul>
      <div className="mt-auto space-y-2 border-t border-hairline px-4 pt-4">
        {failed?.some((s) => s.source_type === "play_store") ? (
          <div className="flex items-start gap-2 font-label-md text-label-md text-error">
            <Icon name="error" className="text-[16px]" />
            Play Store unavailable
          </div>
        ) : null}
        <div className="flex items-center gap-2 font-label-md text-label-md text-on-surface-variant">
          <Icon name="update" className="shrink-0 text-[16px]" />
          <span className="min-w-0 break-words">
            Ingest {relativeTime(overview?.last_ingest?.finished_at)}
          </span>
        </div>
        <div className="flex items-center gap-2 font-label-md text-label-md text-on-surface-variant">
          <Icon name="refresh" className="shrink-0 text-[16px]" />
          <span className="min-w-0 break-words">
            Themes {themes?.themes_refreshed_at ? relativeTime(themes.themes_refreshed_at) : "—"}
          </span>
        </div>
      </div>
    </nav>
  );
}
