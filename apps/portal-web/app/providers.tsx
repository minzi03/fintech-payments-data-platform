"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { PortalApiError } from "@/api/problem";

function retryDelay(attempt: number): number {
  return Math.min(500 * 2 ** attempt, 4_000);
}

export function shouldRetry(failureCount: number, error: Error): boolean {
  if (failureCount >= 2) {
    return false;
  }
  if (error instanceof PortalApiError) {
    if (error.status && [400, 401, 403, 404, 405, 422].includes(error.status)) {
      return false;
    }
    return error.retryable;
  }
  return true;
}

function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        gcTime: 5 * 60_000,
        refetchOnWindowFocus: false,
        retry: shouldRetry,
        retryDelay,
        staleTime: 10_000,
      },
      mutations: {
        retry: false,
      },
    },
  });
}

export function Providers({ children }: { children: ReactNode }) {
  const [queryClient] = useState(createQueryClient);
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}
