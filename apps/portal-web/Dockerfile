# syntax=docker/dockerfile:1.23@sha256:2780b5c3bab67f1f76c781860de469442999ed1a0d7992a5efdf2cffc0e3d769

ARG SOURCE_DATE_EPOCH=0

FROM node:22.20.0-bookworm-slim@sha256:b21fe589dfbe5cc39365d0544b9be3f1f33f55f3c86c87a76ff65a02f8f5848e AS dependencies
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
ARG PORTAL_IDP_PUBLIC_URL=http://portal-idp.localhost:8081
ARG SOURCE_DATE_EPOCH
ENV NEXT_TELEMETRY_DISABLED=1
ENV SOURCE_DATE_EPOCH=$SOURCE_DATE_EPOCH
ENV NEXT_PUBLIC_PORTAL_WEB_VERSION=$PORTAL_WEB_VERSION
ENV NEXT_PUBLIC_PORTAL_BUILD_SHA=$PORTAL_BUILD_SHA
ENV NEXT_PUBLIC_PORTAL_ENV=$PORTAL_ENV
ENV PORTAL_API_INTERNAL_URL=$PORTAL_API_INTERNAL_URL
ENV PORTAL_IDP_PUBLIC_URL=$PORTAL_IDP_PUBLIC_URL
COPY apps/portal-web apps/portal-web
COPY packages/portal-contracts packages/portal-contracts
RUN pnpm --filter @fintech/portal-web build

FROM node:22.20.0-bookworm-slim@sha256:b21fe589dfbe5cc39365d0544b9be3f1f33f55f3c86c87a76ff65a02f8f5848e AS runtime
ENV HOSTNAME=0.0.0.0
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3000
RUN groupadd --system --gid 10001 portal \
    && useradd --system --uid 10001 --gid portal --home-dir /nonexistent portal \
    && rm -rf /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/corepack \
    && rm -f /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack \
        /usr/local/bin/yarn /usr/local/bin/yarnpkg \
    && find /bin /sbin /usr/bin /usr/sbin -xdev -type f \
        \( -perm -4000 -o -perm -2000 \) -exec chmod a-s {} +
WORKDIR /app
COPY --from=build /workspace/apps/portal-web/.next/standalone ./
COPY --from=build /workspace/apps/portal-web/.next/static \
    ./apps/portal-web/.next/static
RUN find /app -xdev -type d -exec chmod 0755 {} + \
    && find /app -xdev -type f -exec chmod 0644 {} +
USER 10001:10001
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=5 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:3000/health').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"]
CMD ["node", "apps/portal-web/server.js"]
