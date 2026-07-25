export type PublicPortalConfig = Readonly<{
  environment: string;
  webVersion: string;
  buildSha: string;
  apiBaseUrl: string;
  developerNavigationEnabled: boolean;
}>;

function safeBuildValue(value: string | undefined, fallback: string): string {
  const normalized = value?.trim();
  return normalized && /^[A-Za-z0-9._-]{1,80}$/.test(normalized) ? normalized : fallback;
}

export const portalConfig: PublicPortalConfig = Object.freeze({
  environment: safeBuildValue(process.env.NEXT_PUBLIC_PORTAL_ENV, "local"),
  webVersion: safeBuildValue(process.env.NEXT_PUBLIC_PORTAL_WEB_VERSION, "0.1.0-dev"),
  buildSha: safeBuildValue(process.env.NEXT_PUBLIC_PORTAL_BUILD_SHA, "local"),
  apiBaseUrl: "/portal-api",
  developerNavigationEnabled: process.env.NODE_ENV !== "production",
});
