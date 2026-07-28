import type { NextConfig } from "next";

import {
  loadPortalWebBuildConfiguration,
  loadPortalWebRuntimeConfiguration,
} from "./config/server";

const production = process.env.NODE_ENV === "production";
const buildConfiguration = loadPortalWebBuildConfiguration();
const runtimeConfiguration = loadPortalWebRuntimeConfiguration();

const contentSecurityPolicy = [
  "default-src 'self'",
  production
    ? "script-src 'self' 'unsafe-inline'"
    : "script-src 'self' 'unsafe-eval' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "font-src 'self'",
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  `form-action 'self' ${buildConfiguration.identityProviderOrigin}`,
  "frame-ancestors 'none'",
].join("; ");

const nextConfig: NextConfig = {
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  generateBuildId: async () => buildConfiguration.buildSha,
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "Content-Security-Policy", value: contentSecurityPolicy },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          {
            key: "Permissions-Policy",
            value: "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
          },
        ],
      },
    ];
  },
  async rewrites() {
    return [
      {
        source: "/portal-api/:path*",
        destination: `${runtimeConfiguration.apiInternalUrl.replace(/\/$/, "")}/:path*`,
      },
    ];
  },
};

export default nextConfig;
