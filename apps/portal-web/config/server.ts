import {
  type EnvironmentSource,
  parseBoundedLabel,
  parseHttpUrl,
  parsePortalEnvironment,
  type PortalEnvironment,
} from "./shared";

export type PortalWebBuildConfiguration = Readonly<{
  environment: PortalEnvironment;
  webVersion: string;
  buildSha: string;
  identityProviderOrigin: string;
}>;

export type PortalWebRuntimeConfiguration = Readonly<{
  apiInternalUrl: string;
  publicOrigin: string;
  host: string;
  port: number;
}>;

export type PortalWebTestConfiguration = Readonly<{
  webUrl: string;
  externalStack: boolean;
  authenticated: boolean;
}>;

export function loadPortalWebBuildConfiguration(
  source: EnvironmentSource = process.env,
): PortalWebBuildConfiguration {
  const environment = parsePortalEnvironment(source.NEXT_PUBLIC_PORTAL_ENV);
  const identityProvider = parseHttpUrl(
    "IDP_PUBLIC_URL",
    source.PORTAL_IDP_PUBLIC_URL,
    "http://portal-idp.localhost:8081",
    { originOnly: true },
  );
  if (
    identityProvider.protocol !== "https:" &&
    !(identityProvider.protocol === "http:" && environment === "local")
  ) {
    throw new Error("PORTAL_WEB_CONFIG_IDP_HTTPS_REQUIRED");
  }
  return Object.freeze({
    environment,
    webVersion: parseBoundedLabel(
      "WEB_VERSION",
      source.NEXT_PUBLIC_PORTAL_WEB_VERSION,
      "0.1.0-dev",
    ),
    buildSha: parseBoundedLabel("BUILD_SHA", source.NEXT_PUBLIC_PORTAL_BUILD_SHA, "local"),
    identityProviderOrigin: identityProvider.origin,
  });
}

export function loadPortalWebRuntimeConfiguration(
  source: EnvironmentSource = process.env,
): PortalWebRuntimeConfiguration {
  const apiInternalUrl = parseHttpUrl(
    "API_INTERNAL_URL",
    source.PORTAL_API_INTERNAL_URL,
    "http://127.0.0.1:8010",
  );
  const publicOrigin = parseHttpUrl(
    "PUBLIC_ORIGIN",
    source.PORTAL_PUBLIC_ORIGIN,
    "http://localhost:3000",
    { originOnly: true },
  );
  const rawPort = source.PORTAL_WEB_PORT?.trim() || "3000";
  const port = Number(rawPort);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error("PORTAL_WEB_CONFIG_INVALID_WEB_PORT");
  }
  return Object.freeze({
    apiInternalUrl: apiInternalUrl.toString(),
    publicOrigin: publicOrigin.origin,
    host: source.PORTAL_WEB_HOST?.trim() || "0.0.0.0",
    port,
  });
}

export function loadPortalWebTestConfiguration(
  source: EnvironmentSource = process.env,
): PortalWebTestConfiguration {
  const webUrl = parseHttpUrl("TEST_WEB_URL", source.PORTAL_WEB_URL, "http://127.0.0.1:3000");
  return Object.freeze({
    webUrl: webUrl.toString(),
    externalStack: Boolean(source.PORTAL_E2E_EXTERNAL),
    authenticated: Boolean(source.PORTAL_E2E_AUTH),
  });
}
