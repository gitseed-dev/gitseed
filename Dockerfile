# syntax=docker/dockerfile:1
# ==============================================================================
#
# GitSeed control plane: API, scheduler, MCP server, CLI and web app
#   docker build -t gitseed/gitseed .
#   docker run gitseed/gitseed [api|web|scheduler|mcp|migrate|<command>]
#
# The agent sandbox is built separately: apps/runner/Dockerfile
#
# ==============================================================================

ARG PYTHON_VERSION=3.12
ARG NODE_VERSION=22
ARG UV_VERSION=0.9
ARG CADDY_VERSION=2

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv
FROM caddy:${CADDY_VERSION}-alpine AS caddy


FROM node:${NODE_VERSION}-slim AS web-build
WORKDIR /src
RUN corepack enable && corepack prepare pnpm@10.34.9 --activate
COPY pnpm-workspace.yaml pnpm-lock.yaml ./
COPY apps/web/package.json apps/web/
RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile --filter web
COPY apps/web apps/web
ARG GITSEED_VERSION=dev
ENV VITE_GITSEED_VERSION=${GITSEED_VERSION}
RUN pnpm --filter web build


FROM python:${PYTHON_VERSION}-slim AS py-build
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app

COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
COPY apps/core/pyproject.toml apps/core/README.md apps/core/LICENSE apps/core/NOTICE apps/core/
COPY apps/mcp/pyproject.toml apps/mcp/README.md apps/mcp/LICENSE apps/mcp/NOTICE apps/mcp/
COPY apps/cli/pyproject.toml apps/cli/README.md apps/cli/LICENSE apps/cli/NOTICE apps/cli/
COPY apps/runner/pyproject.toml apps/runner/README.md apps/runner/LICENSE apps/runner/NOTICE apps/runner/
COPY packages/common/pyproject.toml packages/common/README.md packages/common/LICENSE packages/common/NOTICE packages/common/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --all-packages --no-install-workspace

COPY apps/core apps/core
COPY apps/mcp apps/mcp
COPY apps/cli apps/cli
COPY apps/runner apps/runner
COPY packages/common packages/common
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --all-packages --no-editable


FROM python:${PYTHON_VERSION}-slim AS gitseed
ARG GITSEED_VERSION=dev
LABEL org.opencontainers.image.source="https://github.com/gitseed-dev/gitseed" \
      org.opencontainers.image.licenses="Apache-2.0"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    XDG_DATA_HOME=/app/data \
    XDG_CONFIG_HOME=/app/data \
    HOST=0.0.0.0 \
    API_PORT=8420 \
    WEB_PORT=8421 \
    MCP_PORT=8422 \
    SANDBOX_PORT=8423 \
    GITSEED_VERSION=${GITSEED_VERSION}

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git procps \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 1000 gitseed \
    && useradd -u 1000 -g gitseed -m -s /usr/sbin/nologin gitseed \
    && mkdir -p /app/data && chown -R gitseed:gitseed /app/data

WORKDIR /app
COPY --from=caddy /usr/bin/caddy /usr/bin/caddy
COPY --from=py-build /app/.venv /app/.venv
COPY --from=web-build /src/apps/web/dist /app/web
COPY apps/web/Caddyfile /app/Caddyfile
COPY apps/core/alembic.ini /app/alembic.ini
COPY LICENSE NOTICE /app/
COPY --chmod=755 entrypoint.sh /app/entrypoint.sh
RUN sed -i 's/\r$//' /app/entrypoint.sh && chmod 755 /app/entrypoint.sh

USER 1000:1000
EXPOSE 8420 8421 8422 8423

ENTRYPOINT ["/bin/sh", "/app/entrypoint.sh"]
CMD ["api"]
