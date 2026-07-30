# ── Stage 1: build the React UI ───────────────────────────────────
FROM node:20-slim AS ui-builder
WORKDIR /ui
COPY ui/mira/package.json ui/mira/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY ui/mira/ ./
RUN npm run build

FROM node:22-slim AS pi-worker-builder
WORKDIR /worker
COPY integrations/mira-pi-worker/package.json integrations/mira-pi-worker/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund
COPY integrations/mira-pi-worker/worker.js ./

# ── Stage 2: backend + bundled UI ─────────────────────────────────
FROM python:3.12-slim
LABEL org.opencontainers.image.source="https://github.com/miracodeai/mira"
LABEL org.opencontainers.image.description="Self-hostable AI code reviewer"
LABEL org.opencontainers.image.licenses="Apache-2.0"

WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir "/app[serve,bedrock]"

# Pull the built UI in from stage 1. webhooks.create_app() picks this up
# automatically and serves it at / with SPA fallback.
COPY --from=ui-builder /ui/dist /app/ui_dist
COPY --from=pi-worker-builder /usr/local/bin/node /usr/local/bin/node
COPY --from=pi-worker-builder /worker /opt/mira-pi-worker
RUN ln -s /opt/mira-pi-worker/worker.js /usr/local/bin/mira-pi-worker

EXPOSE 8000
# ENTRYPOINT (not CMD) so `docker run … image --config /app/mira.yaml`
# appends the args to `mira serve` instead of replacing the command.
ENTRYPOINT ["mira", "serve"]
