# ------------------------------------------------------------------------------
# Public Frontend — Next.js App Router Marketing + App Shell
# ------------------------------------------------------------------------------
# Build context: ./frontend-public
# Production runtime: node (Next.js standalone)
# ------------------------------------------------------------------------------

# ── Build stage ───────────────────────────────────────────────────────────────
FROM node:22-alpine AS builder

WORKDIR /app

# Run pnpm in non-interactive CI mode
ENV CI=true

# Install dependencies first to leverage Docker cache
# frontend-public 不再是独立的 pnpm workspace（见 docs/architecture/frontend-architecture.md），
# 镜像里没有仓根的 pnpm-workspace.yaml / lockfile，这里就是它自己的安装目录。
COPY package.json ./
RUN npm install -g pnpm && pnpm install

# Copy source and build
COPY . .
RUN pnpm build

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM node:22-alpine AS runner

WORKDIR /app

ENV NODE_ENV=production
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

# Next.js standalone output
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

EXPOSE 3000

CMD ["node", "server.js"]
