# syntax=docker/dockerfile:1
# ==============================================================================
#
# GitSeed Agent Runner Sandbox
#   docker build -t gitseed/runner:dev apps/runner
#
# Runs ephemeral stage jobs (ideate, plan, execute, review, merge) inside an
# isolated non-root container connected only to the gitseed-runners network.
#
# Third-party agent CLIs:
#   - Pre-installed out of the box: opencode, Codex (@openai/codex), Gemini CLI
#     (@google/gemini-cli), and Claude Code (@anthropic-ai/claude-code, enabled by
#     default via INCLUDE_PROPRIETARY_CLIS=true; disable with
#     --build-arg INCLUDE_PROPRIETARY_CLIS=false when publishing redistributed images).
#   - First-class adapters with custom binary / installer URL support:
#     Google Antigravity (agy) and xAI Grok Build (grok) are proprietary vendor CLIs
#     without public npm packages. Supply --build-arg ANTIGRAVITY_INSTALL_URL=<url>
#     or --build-arg GROK_CLI_INSTALL_URL=<url> at build time, or layer the vendor
#     binaries onto gitseed/runner in a derived Dockerfile.
#
# ==============================================================================

ARG PYTHON_VERSION=3.12
ARG NODE_VERSION=22
ARG UV_VERSION=0.9

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv
FROM node:${NODE_VERSION}-slim AS node


FROM python:${PYTHON_VERSION}-slim AS py-build
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app

COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /app/.venv && \
    uv pip install --python /app/.venv/bin/python --no-cache .


FROM python:${PYTHON_VERSION}-slim AS runner
ARG GITSEED_VERSION=dev
LABEL org.opencontainers.image.source="https://github.com/gitseed-dev/gitseed" \
      org.opencontainers.image.licenses="Apache-2.0"
# Bounded semver ranges for pre-installed agent CLIs (override via `--build-arg <NAME>_VERSION=<ver>`).
ARG OPENCODE_VERSION=">=1.0.0 <2.0.0"
ARG CLAUDE_CODE_VERSION=">=1.0.0 <3.0.0"
ARG INCLUDE_PROPRIETARY_CLIS=true
ARG INSTALL_CLAUDE_CODE=${INCLUDE_PROPRIETARY_CLIS}
ARG INSTALL_ANTIGRAVITY=${INCLUDE_PROPRIETARY_CLIS}
ARG INSTALL_GROK_CLI=${INCLUDE_PROPRIETARY_CLIS}
# Optional HTTPS installer script URLs for bring-your-own Antigravity (agy) and Grok (grok) binaries.
# Leave empty for standard builds or pass --build-arg ANTIGRAVITY_INSTALL_URL=... / GROK_CLI_INSTALL_URL=...
ARG ANTIGRAVITY_INSTALL_URL=""
ARG GROK_CLI_INSTALL_URL=""
ARG CODEX_VERSION=">=0.40.0 <2.0.0"
ARG GEMINI_CLI_VERSION=">=0.10.0 <2.0.0"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:/home/runner/.local/bin:/usr/local/bin:$PATH" \
    HOME=/home/runner \
    GITSEED_WORKSPACE=/workspace \
    GITSEED_CTL_DIR=/gitseed-ctl \
    GITSEED_SECRETS_DIR=/gitseed-secrets \
    GITSEED_RUNNER_MODE=agent \
    GITSEED_VERSION=${GITSEED_VERSION}

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git openssh-client ripgrep \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g \
    "opencode-ai@${OPENCODE_VERSION}" \
    "@openai/codex@${CODEX_VERSION}" \
    "@google/gemini-cli@${GEMINI_CLI_VERSION}" \
    && if [ "${INSTALL_CLAUDE_CODE}" = "true" ]; then \
         npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}"; \
       fi \
    && npm cache clean --force \
    && groupadd -g 1000 runner \
    && useradd -u 1000 -g runner -m -s /bin/bash runner \
    && mkdir -p /workspace /gitseed-ctl /gitseed-secrets /home/runner/.local/bin /home/runner/.cache/packages \
    # Antigravity (agy) and Grok Build (grok) have first-class runtime adapters; install
    # them automatically when ANTIGRAVITY_INSTALL_URL / GROK_CLI_INSTALL_URL are provided,
    # otherwise operators can layer vendor binaries onto a derived image.
    && if [ "${INSTALL_ANTIGRAVITY}" = "true" ] && [ -n "${ANTIGRAVITY_INSTALL_URL}" ]; then \
         curl -fsSL "${ANTIGRAVITY_INSTALL_URL}" -o /tmp/install-agy.sh \
         && HOME=/home/runner bash /tmp/install-agy.sh \
         && rm -f /tmp/install-agy.sh \
         && if [ -f /home/runner/.local/bin/agy ]; then ln -sf /home/runner/.local/bin/agy /usr/local/bin/agy; fi \
         && command -v agy >/dev/null 2>&1; \
       fi \
    && if [ "${INSTALL_GROK_CLI}" = "true" ] && [ -n "${GROK_CLI_INSTALL_URL}" ]; then \
         curl -fsSL "${GROK_CLI_INSTALL_URL}" -o /tmp/install-grok.sh \
         && HOME=/home/runner bash /tmp/install-grok.sh \
         && rm -f /tmp/install-grok.sh \
         && if [ -f /home/runner/.local/bin/grok ]; then ln -sf /home/runner/.local/bin/grok /usr/local/bin/grok; fi \
         && command -v grok >/dev/null 2>&1; \
       fi \
    && chown -R 1000:1000 /workspace /gitseed-ctl /gitseed-secrets /home/runner

COPY --from=py-build /app/.venv /app/.venv
COPY LICENSE NOTICE /app/

# Numeric UID:GID (runner=1000:1000) required by Kubernetes PodSecurity runAsNonRoot
USER 1000:1000
WORKDIR /workspace

ENTRYPOINT ["python", "-m", "gitseed_runner.main"]
