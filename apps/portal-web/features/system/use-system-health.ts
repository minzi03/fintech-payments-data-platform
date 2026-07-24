"use client";

import { useQuery } from "@tanstack/react-query";

import { portalApi } from "@/api/portal-api";
import { portalQueryKeys } from "@/api/query-keys";

export function useSystemHealth() {
  const liveness = useQuery({
    queryKey: portalQueryKeys.liveness(),
    queryFn: portalApi.liveness,
  });
  const readiness = useQuery({
    queryKey: portalQueryKeys.readiness(),
    queryFn: portalApi.readiness,
  });
  const systemInfo = useQuery({
    queryKey: portalQueryKeys.systemInfo(),
    queryFn: portalApi.systemInfo,
  });
  const dependencies = useQuery({
    queryKey: portalQueryKeys.dependencies(),
    queryFn: () => portalApi.dependencies(),
  });
  return { dependencies, liveness, readiness, systemInfo };
}
