"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

export function QueryProvider({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            retry: (count, error) => {
              const status = (error as { status?: number }).status;
              if (status === 401 || status === 404) return false;
              if (status === 503) return count < 10;
              return count < 1;
            },
            retryDelay: (attempt, error) => {
              const status = (error as { status?: number }).status;
              if (status === 503) return Math.min(2000 * (attempt + 1), 8000);
              return 1000;
            },
            refetchOnWindowFocus: false,
          },
        },
      }),
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
