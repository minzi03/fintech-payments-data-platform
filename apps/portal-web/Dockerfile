# syntax=docker/dockerfile:1.7

FROM node:22.20.0-bookworm-slim AS dependencies
WORKDIR /workspace
ENV PNPM_HOME=/pnpm
ENV PATH=$PNPM_HOME:$PATH
RUN corepack enable && corepack prepare pnpm@11.9.0 --activate
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/portal-web/package.json apps/portal-web/package.json
COPY packages/portal-contracts/package.json packages/portal-contracts/package.json
RUN pnpm install --frozen-lockfile

FROM dependencies AS build
ARG PORTAL_WEB_VERSION=0.1.0-dev
ARG PORTAL_BUILD_SHA=local
ARG PORTAL_ENV=local
ARG PORTAL_API_INTERNAL_URL=http://portal-api:8010
ENV NEXT_TELEMETRY_DISABLED=1
ENV NEXT_PUBLIC_PORTAL_WEB_VERSION=$PORTAL_WEB_VERSION
ENV NEXT_PUBLIC_PORTAL_BUILD_SHA=$PORTAL_BUILD_SHA
ENV NEXT_PUBLIC_PORTAL_ENV=$PORTAL_ENV
ENV PORTAL_API_INTERNAL_URL=$PORTAL_API_INTERNAL_URL
COPY apps/portal-web apps/portal-web
COPY packages/portal-contracts packages/portal-contracts
RUN pnpm --filter @fintech/portal-web build

FROM node:22.20.0-bookworm-slim AS runtime
ENV HOSTNAME=0.0.0.0
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3000
RUN groupadd --system --gid 10001 portal \
    && useradd --system --uid 10001 --gid portal --home-dir /nonexistent portal
WORKDIR /app
COPY --from=build --chown=portal:portal /workspace/apps/portal-web/.next/standalone ./
COPY --from=build --chown=portal:portal /workspace/apps/portal-web/.next/static \
    ./apps/portal-web/.next/static
USER 10001:10001
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=5 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:3000/health').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"]
CMD ["node", "apps/portal-web/server.js"]
