"use client";

import type { CapabilityView, EnvironmentView, SessionView } from "@fintech/portal-contracts";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

import { portalApi } from "@/api/portal-api";
import { PortalApiError } from "@/api/problem";
import { portalQueryKeys } from "@/api/query-keys";

type SessionState = "loading" | "anonymous" | "authenticated" | "error";

type PortalSessionContextValue = {
  capabilities: ReadonlyArray<CapabilityView>;
  environments: ReadonlyArray<EnvironmentView>;
  error: Error | null;
  isMutating: boolean;
  logout: () => Promise<void>;
  logoutAll: () => Promise<void>;
  refreshSession: () => Promise<void>;
  selectEnvironment: (environmentId: string) => Promise<void>;
  selectedEnvironment: string | null;
  session: SessionView | null;
  state: SessionState;
};

const anonymousContext: PortalSessionContextValue = {
  capabilities: [],
  environments: [],
  error: null,
  isMutating: false,
  logout: async () => undefined,
  logoutAll: async () => undefined,
  refreshSession: async () => undefined,
  selectEnvironment: async () => undefined,
  selectedEnvironment: null,
  session: null,
  state: "anonymous",
};

const PortalSessionContext = createContext<PortalSessionContextValue>(anonymousContext);

async function currentSession(): Promise<SessionView | null> {
  try {
    return await portalApi.session();
  } catch (error) {
    if (error instanceof PortalApiError && error.status === 401) {
      return null;
    }
    throw error;
  }
}

export function PortalSessionProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [selectedEnvironment, setSelectedEnvironment] = useState<string | null>(null);
  const sessionQuery = useQuery({
    queryKey: portalQueryKeys.session(),
    queryFn: currentSession,
  });
  const authenticated = Boolean(sessionQuery.data);
  const environmentsQuery = useQuery({
    queryKey: portalQueryKeys.environments(),
    queryFn: portalApi.environments,
    enabled: authenticated,
  });
  const capabilitiesQuery = useQuery({
    queryKey: portalQueryKeys.capabilities(selectedEnvironment ?? undefined),
    queryFn: () => portalApi.capabilities(selectedEnvironment as string),
    enabled: authenticated && selectedEnvironment !== null,
  });

  const selectMutation = useMutation({
    mutationFn: (environmentId: string) => portalApi.selectEnvironment(environmentId),
    onSuccess: (selection) => {
      setSelectedEnvironment(selection.environment_id);
      queryClient.removeQueries({ queryKey: portalQueryKeys.dependencies() });
    },
  });
  const refreshMutation = useMutation({
    mutationFn: () => portalApi.refreshSession(),
    onSuccess: (session) => {
      queryClient.setQueryData(portalQueryKeys.session(), session);
    },
  });
  const logoutMutation = useMutation({
    mutationFn: () => portalApi.logout(),
    onSuccess: () => {
      setSelectedEnvironment(null);
      queryClient.setQueryData(portalQueryKeys.session(), null);
      queryClient.removeQueries({ queryKey: portalQueryKeys.environments() });
    },
  });
  const logoutAllMutation = useMutation({
    mutationFn: () => portalApi.logoutAll(),
    onSuccess: () => {
      setSelectedEnvironment(null);
      queryClient.setQueryData(portalQueryKeys.session(), null);
      queryClient.removeQueries({ queryKey: portalQueryKeys.environments() });
    },
  });

  const mutationError =
    selectMutation.error ??
    refreshMutation.error ??
    logoutMutation.error ??
    logoutAllMutation.error ??
    null;
  const state: SessionState = sessionQuery.isPending
    ? "loading"
    : sessionQuery.error
      ? "error"
      : authenticated
        ? "authenticated"
        : "anonymous";
  const value = useMemo<PortalSessionContextValue>(
    () => ({
      capabilities: capabilitiesQuery.data?.capabilities ?? [],
      environments: environmentsQuery.data?.environments ?? [],
      error: sessionQuery.error ?? mutationError,
      isMutating:
        selectMutation.isPending ||
        refreshMutation.isPending ||
        logoutMutation.isPending ||
        logoutAllMutation.isPending,
      logout: async () => {
        await logoutMutation.mutateAsync();
      },
      logoutAll: async () => {
        await logoutAllMutation.mutateAsync();
      },
      refreshSession: async () => {
        await refreshMutation.mutateAsync();
      },
      selectEnvironment: async (environmentId: string) => {
        await selectMutation.mutateAsync(environmentId);
      },
      selectedEnvironment,
      session: sessionQuery.data ?? null,
      state,
    }),
    [
      capabilitiesQuery.data,
      environmentsQuery.data,
      logoutAllMutation,
      logoutMutation,
      mutationError,
      refreshMutation,
      selectedEnvironment,
      selectMutation,
      sessionQuery.data,
      sessionQuery.error,
      state,
    ],
  );

  return <PortalSessionContext.Provider value={value}>{children}</PortalSessionContext.Provider>;
}

export function usePortalSession(): PortalSessionContextValue {
  return useContext(PortalSessionContext);
}
