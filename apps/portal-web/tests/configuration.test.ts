import { describe, expect, it } from "vitest";

import { loadPublicPortalConfig } from "../config/public";
import {
  loadPortalWebBuildConfiguration,
  loadPortalWebRuntimeConfiguration,
  loadPortalWebTestConfiguration,
} from "../config/server";

describe("Portal Web configuration", () => {
  it("separates public build labels from server-only values", () => {
    const publicConfiguration = loadPublicPortalConfig(
      {
        NEXT_PUBLIC_PORTAL_ENV: "development",
        NEXT_PUBLIC_PORTAL_WEB_VERSION: "1.2.3",
        NEXT_PUBLIC_PORTAL_BUILD_SHA: "abc123",
        PORTAL_API_INTERNAL_URL: "http://private-api:8010",
      },
      "production",
    );

    expect(publicConfiguration).toEqual({
      environment: "development",
      webVersion: "1.2.3",
      buildSha: "abc123",
      apiBaseUrl: "/portal-api",
      developerNavigationEnabled: false,
    });
    expect(JSON.stringify(publicConfiguration)).not.toContain("private-api");
    expect(Object.isFrozen(publicConfiguration)).toBe(true);
  });

  it("loads immutable build and server-runtime domains", () => {
    const build = loadPortalWebBuildConfiguration({
      NEXT_PUBLIC_PORTAL_ENV: "staging",
      NEXT_PUBLIC_PORTAL_WEB_VERSION: "2.0.0",
      NEXT_PUBLIC_PORTAL_BUILD_SHA: "deadbeef",
      PORTAL_IDP_PUBLIC_URL: "https://identity.example",
    });
    const runtime = loadPortalWebRuntimeConfiguration({
      PORTAL_API_INTERNAL_URL: "http://portal-api:8010",
      PORTAL_PUBLIC_ORIGIN: "https://portal.example",
      PORTAL_WEB_HOST: "0.0.0.0",
      PORTAL_WEB_PORT: "3001",
    });

    expect(build.identityProviderOrigin).toBe("https://identity.example");
    expect(runtime).toEqual({
      apiInternalUrl: "http://portal-api:8010/",
      publicOrigin: "https://portal.example",
      host: "0.0.0.0",
      port: 3001,
    });
    expect(Object.isFrozen(build)).toBe(true);
    expect(Object.isFrozen(runtime)).toBe(true);
  });

  it("rejects malformed and unsafe configuration", () => {
    expect(() =>
      loadPortalWebBuildConfiguration({
        NEXT_PUBLIC_PORTAL_ENV: "production",
        PORTAL_IDP_PUBLIC_URL: "http://identity.example",
      }),
    ).toThrow("PORTAL_WEB_CONFIG_IDP_HTTPS_REQUIRED");
    expect(() =>
      loadPortalWebRuntimeConfiguration({
        PORTAL_API_INTERNAL_URL: "file:///tmp/socket",
      }),
    ).toThrow("PORTAL_WEB_CONFIG_INVALID_API_INTERNAL_URL");
    expect(() =>
      loadPortalWebRuntimeConfiguration({
        PORTAL_PUBLIC_ORIGIN: "https://portal.example/path",
      }),
    ).toThrow("PORTAL_WEB_CONFIG_INVALID_PUBLIC_ORIGIN");
    expect(() =>
      loadPortalWebRuntimeConfiguration({
        PORTAL_WEB_PORT: "70000",
      }),
    ).toThrow("PORTAL_WEB_CONFIG_INVALID_WEB_PORT");
  });

  it("keeps E2E-only settings in a distinct model", () => {
    const configuration = loadPortalWebTestConfiguration({
      PORTAL_WEB_URL: "http://127.0.0.1:3100",
      PORTAL_E2E_EXTERNAL: "1",
      PORTAL_E2E_AUTH: "1",
    });

    expect(configuration).toEqual({
      webUrl: "http://127.0.0.1:3100/",
      externalStack: true,
      authenticated: true,
    });
  });
});
