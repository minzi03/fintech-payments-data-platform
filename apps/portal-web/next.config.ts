import type { NextConfig } from "next";

const production = process.env.NODE_ENV === "production";
const apiTarget = process.env.PORTAL_API_INTERNAL_URL ?? "http://127.0.0.1:8010";
const portalEnvironment = process.env.NEXT_PUBLIC_PORTAL_ENV ?? "local";

function identityProviderOrigin(): string {
  const configured = process.env.PORTAL_IDP_PUBLIC_URL ?? "http://portal-idp.localhost:8081";
  const url = new URL(configured);
  if (url.protocol !== "https:" && !(url.protocol === "http:" && portalEnvironment === "local")) {
    throw new Error("The identity-provider form action must use HTTPS outside local development.");
  }
  return url.origin;
}

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
  `form-action 'self' ${identityProviderOrigin()}`,
  "frame-ancestors 'none'",
].join("; ");

const nextConfig: NextConfig = {
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  generateBuildId: async () => process.env.NEXT_PUBLIC_PORTAL_BUILD_SHA ?? "local",
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
        destination: `${apiTarget}/:path*`,
      },
    ];
  },
};

export default nextConfig;
