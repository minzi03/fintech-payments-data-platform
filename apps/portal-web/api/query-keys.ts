export const portalQueryKeys = {
  all: ["portal-api"] as const,
  liveness: () => [...portalQueryKeys.all, "liveness"] as const,
  readiness: () => [...portalQueryKeys.all, "readiness"] as const,
  systemInfo: () => [...portalQueryKeys.all, "system-info"] as const,
  dependencies: (environmentId?: string) =>
    [...portalQueryKeys.all, "dependencies", environmentId ?? null] as const,
  session: () => [...portalQueryKeys.all, "session"] as const,
  environments: () => [...portalQueryKeys.all, "environments"] as const,
  capabilities: (environmentId?: string) =>
    [...portalQueryKeys.all, "capabilities", environmentId ?? null] as const,
  navigation: (environmentId?: string) =>
    [...portalQueryKeys.all, "navigation", environmentId ?? null] as const,
};
