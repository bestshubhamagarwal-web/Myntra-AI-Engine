import { Suspense, type ReactNode } from "react";

import { AppShell } from "@/components/AppShell";
import { PageSkeleton } from "@/components/States";

export const dynamic = "force-dynamic";

export default function ShellLayout({ children }: { children: ReactNode }) {
  return (
    <Suspense fallback={<PageSkeleton />}>
      <AppShell>{children}</AppShell>
    </Suspense>
  );
}
