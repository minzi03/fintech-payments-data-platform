export const portalEnvironments = [
  "local",
  "test",
  "development",
  "staging",
  "production",
] as const;

export type PortalEnvironment = (typeof portalEnvironments)[number];
export type EnvironmentSource = Readonly<Record<string, string | undefined>>;

const boundedLabel = /^[A-Za-z0-9._-]{1,80}$/;

export function parsePortalEnvironment(
  value: string | undefined,
  fallback: PortalEnvironment = "local",
): PortalEnvironment {
  const normalized = value?.trim();
  if (!normalized) {
    return fallback;
  }
  if (!portalEnvironments.includes(normalized as PortalEnvironment)) {
    throw new Error("PORTAL_WEB_CONFIG_INVALID_ENVIRONMENT");
  }
  return normalized as PortalEnvironment;
}

export function parseBoundedLabel(
  name: string,
  value: string | undefined,
  fallback: string,
): string {
  const normalized = value?.trim() || fallback;
  if (!boundedLabel.test(normalized)) {
    throw new Error(`PORTAL_WEB_CONFIG_INVALID_${name}`);
  }
  return normalized;
}

export function parseHttpUrl(
  name: string,
  value: string | undefined,
  fallback: string,
  options: Readonly<{ originOnly?: boolean }> = {},
): URL {
  let url: URL;
  try {
    url = new URL(value?.trim() || fallback);
  } catch {
    throw new Error(`PORTAL_WEB_CONFIG_INVALID_${name}`);
  }
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) {
    throw new Error(`PORTAL_WEB_CONFIG_INVALID_${name}`);
  }
  if (
    options.originOnly &&
    (url.pathname !== "/" || url.search.length > 0 || url.hash.length > 0)
  ) {
    throw new Error(`PORTAL_WEB_CONFIG_INVALID_${name}`);
  }
  return url;
}
