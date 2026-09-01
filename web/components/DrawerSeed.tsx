"use client";

import { createContext, useContext, useState, type ReactNode } from "react";

import type { Citation, EvidenceRow } from "@/lib/types";

type Seed = Citation | EvidenceRow | null;

const DrawerSeedContext = createContext<{
  seed: Seed;
  setSeed: (seed: Seed) => void;
}>({ seed: null, setSeed: () => undefined });

export function DrawerSeedProvider({ children }: { children: ReactNode }) {
  const [seed, setSeed] = useState<Seed>(null);
  return (
    <DrawerSeedContext.Provider value={{ seed, setSeed }}>{children}</DrawerSeedContext.Provider>
  );
}

export function useDrawerSeed() {
  return useContext(DrawerSeedContext);
}
