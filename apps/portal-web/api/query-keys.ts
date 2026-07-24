export const portalQueryKeys = {
  all: ["portal-api"] as const,
  liveness: () => [...portalQueryKeys.all, "liveness"] as const,
  readiness: () => [...portalQueryKeys.all, "readiness"] as const,
  systemInfo: () => [...portalQueryKeys.all, "system-info"] as const,
  dependencies: () => [...portalQueryKeys.all, "dependencies"] as const,
};
