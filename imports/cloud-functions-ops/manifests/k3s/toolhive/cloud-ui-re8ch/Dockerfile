FROM --platform=$BUILDPLATFORM node:24.18.0-alpine AS builder
RUN apk add --no-cache git && corepack enable && corepack prepare pnpm@10.33.0 --activate
WORKDIR /src
RUN git clone --depth 1 --branch v0.6.2 https://github.com/stacklok/toolhive-cloud-ui.git .
COPY re8ch-codex.patch /tmp/re8ch-codex.patch
RUN git apply /tmp/re8ch-codex.patch
RUN --mount=type=cache,id=pnpm,target=/root/.local/share/pnpm/store pnpm install --frozen-lockfile
RUN pnpm build

FROM node:24.18.0-alpine
WORKDIR /app
ENV NODE_ENV=production PORT=3000 HOSTNAME=0.0.0.0
RUN addgroup --system --gid 1001 nodejs && adduser --system --uid 1001 nextjs
COPY --from=builder --chown=nextjs:nodejs /src/public ./public
COPY --from=builder --chown=nextjs:nodejs /src/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /src/.next/static ./.next/static
USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]
