import { loadPublicPortalConfig, type PublicPortalConfig } from "../config/public";

export type { PublicPortalConfig };

export const portalConfig: PublicPortalConfig = loadPublicPortalConfig(
  {
    NEXT_PUBLIC_PORTAL_ENV: process.env.NEXT_PUBLIC_PORTAL_ENV,
    NEXT_PUBLIC_PORTAL_WEB_VERSION: process.env.NEXT_PUBLIC_PORTAL_WEB_VERSION,
    NEXT_PUBLIC_PORTAL_BUILD_SHA: process.env.NEXT_PUBLIC_PORTAL_BUILD_SHA,
  },
  process.env.NODE_ENV,
);
