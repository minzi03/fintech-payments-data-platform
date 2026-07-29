import {
  type EnvironmentSource,
  parseBoundedLabel,
  parsePortalEnvironment,
  type PortalEnvironment,
} from "./shared";

export type PublicPortalConfig = Readonly<{
  environment: PortalEnvironment;
  webVersion: string;
  buildSha: string;
  apiBaseUrl: "/portal-api";
  developerNavigationEnabled: boolean;
}>;

export function loadPublicPortalConfig(
  source: EnvironmentSource,
  nodeEnvironment: string | undefined,
): PublicPortalConfig {
  return Object.freeze({
    environment: parsePortalEnvironment(source.NEXT_PUBLIC_PORTAL_ENV),
    webVersion: parseBoundedLabel(
      "WEB_VERSION",
      source.NEXT_PUBLIC_PORTAL_WEB_VERSION,
      "0.1.0-dev",
    ),
    buildSha: parseBoundedLabel("BUILD_SHA", source.NEXT_PUBLIC_PORTAL_BUILD_SHA, "local"),
    apiBaseUrl: "/portal-api",
    developerNavigationEnabled: nodeEnvironment !== "production",
  });
}
