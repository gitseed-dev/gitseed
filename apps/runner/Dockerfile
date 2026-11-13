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
#   - Always installed (open-source licenses): opencode, Codex (@openai/codex),
#     Gemini CLI (@google/gemini-cli).
#   - Proprietary vendor CLIs — Claude Code (@anthropic-ai/claude-code), Google
#     Antigravity (agy), xAI Grok Build (grok) — are OFF by default
#     (INCLUDE_PROPRIETARY_CLIS=false) so images you build and push to a
#     registry do not redistribute them. Opt in for local/private images with
#     --build-arg INCLUDE_PROPRIETARY_CLIS=true (the Compose quickstart and
#     `make build` do this explicitly for the locally built image).
#   - Bring-your-own binary / custom installer URL support:
#     Override --build-arg ANTIGRAVITY_INSTALL_URL=<url> or pass
#     --build-arg GROK_CLI_INSTALL_URL=<url> at build time, or layer vendor
#     binaries onto gitseed/runner in a derived Dockerfile.
#   - Installer integrity: the Antigravity/Grok installers are fetched over
#     HTTPS from the vendor at build time and are NOT version-pinned by
#     default. Pin them by passing --build-arg ANTIGRAVITY_INSTALL_SHA256=<sha>
#     / GROK_CLI_INSTALL_SHA256=<sha> (the build fails on mismatch), or point
#     the *_INSTALL_URL args at a vetted, versioned copy you host yourself.
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
ARG INCLUDE_PROPRIETARY_CLIS=false
ARG INSTALL_CLAUDE_CODE=${INCLUDE_PROPRIETARY_CLIS}
ARG INSTALL_ANTIGRAVITY=${INCLUDE_PROPRIETARY_CLIS}
ARG REQUIRE_ANTIGRAVITY_CLI=false
ARG INSTALL_GROK_CLI=${INCLUDE_PROPRIETARY_CLIS}
# HTTPS installer script URLs for Antigravity (agy) and Grok (grok) binaries.
# Antigravity defaults to Google's official Linux installer when INSTALL_ANTIGRAVITY=true.
# TODO(pin): the upstream installer is unversioned; set ANTIGRAVITY_INSTALL_SHA256
# (and GROK_CLI_INSTALL_SHA256) to the sha256 of a reviewed installer script to
# make builds reproducible and fail closed if the script changes.
ARG ANTIGRAVITY_INSTALL_URL="https://antigravity.google/cli/install.sh"
ARG ANTIGRAVITY_INSTALL_SHA256=""
ARG GROK_CLI_INSTALL_URL=""
ARG GROK_CLI_INSTALL_SHA256=""
ARG CODEX_VERSION=">=0.40.0 <2.0.0"
ARG GEMINI_CLI_VERSION=">=0.10.0 <2.0.0"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:/home/runner/.local/bin:/usr/local/bin:$PATH" \
    HOME=/home/runner \
    NODE_USE_ENV_PROXY=1 \
    GEMINI_FORCE_FILE_STORAGE=true \
    GEMINI_FORCE_ENCRYPTED_FILE_STORAGE=false \
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
    ca-certificates curl dbus git gnome-keyring libsecret-1-0 libsecret-tools libstdc++6 openssh-client ripgrep unzip xz-utils \
    && rm -rf /var/lib/apt/lists/* \
    # Strip file capabilities (cap_ipc_lock+ep) from gnome-keyring-daemon so it executes
    # under CapDrop: ALL + no-new-privileges:true inside unprivileged runner containers.
    && if [ -f /usr/bin/gnome-keyring-daemon ]; then \
         cp --no-preserve=xattr /usr/bin/gnome-keyring-daemon /usr/bin/gnome-keyring-daemon.nocap \
         && mv /usr/bin/gnome-keyring-daemon.nocap /usr/bin/gnome-keyring-daemon \
         && chmod 0755 /usr/bin/gnome-keyring-daemon; \
       fi \
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
    && mkdir -p /workspace /gitseed-ctl /gitseed-secrets /home/runner/.local/bin /home/runner/.local/share/keyrings /home/runner/.cache/packages \
    && chown -R 1000:1000 /workspace /gitseed-ctl /gitseed-secrets /home/runner \
    # Install Google Antigravity (agy) and xAI Grok Build (grok) when enabled.
    && if [ "${INSTALL_ANTIGRAVITY}" = "true" ] && [ -n "${ANTIGRAVITY_INSTALL_URL}" ]; then \
         if curl --retry 3 --retry-delay 2 --connect-timeout 15 -fsSL "${ANTIGRAVITY_INSTALL_URL}" -o /tmp/install-agy.sh \
            && { [ -z "${ANTIGRAVITY_INSTALL_SHA256}" ] \
                 || echo "${ANTIGRAVITY_INSTALL_SHA256}  /tmp/install-agy.sh" | sha256sum -c - \
                 || { echo "ERROR: Antigravity installer sha256 mismatch." >&2; exit 1; }; } \
            && chmod 0755 /tmp/install-agy.sh \
            && su -s /bin/bash runner -c "HOME=/home/runner PATH=/home/runner/.local/bin:/usr/local/bin:/usr/bin:/bin bash /tmp/install-agy.sh" \
            && rm -f /tmp/install-agy.sh \
            && (for candidate in /home/runner/.local/bin/agy /home/runner/.antigravity/bin/agy; do \
                  if [ -x "${candidate}" ]; then ln -sf "${candidate}" /usr/local/bin/agy; break; fi; \
                done) \
            && command -v agy >/dev/null 2>&1; then \
           true; \
         elif [ "${REQUIRE_ANTIGRAVITY_CLI}" = "true" ]; then \
           echo "ERROR: Required Antigravity CLI installation from ${ANTIGRAVITY_INSTALL_URL} failed." >&2; exit 1; \
         else \
           rm -f /tmp/install-agy.sh; \
           echo "WARN: Antigravity CLI installation from ${ANTIGRAVITY_INSTALL_URL} unavailable; continuing runner image build without agy (set REQUIRE_ANTIGRAVITY_CLI=true to fail hard)." >&2; \
         fi; \
       fi \
    && if [ "${INSTALL_GROK_CLI}" = "true" ] && [ -n "${GROK_CLI_INSTALL_URL}" ]; then \
         curl -fsSL "${GROK_CLI_INSTALL_URL}" -o /tmp/install-grok.sh \
         && { [ -z "${GROK_CLI_INSTALL_SHA256}" ] \
              || echo "${GROK_CLI_INSTALL_SHA256}  /tmp/install-grok.sh" | sha256sum -c -; } \
         && chmod 0755 /tmp/install-grok.sh \
         && su -s /bin/bash runner -c "HOME=/home/runner PATH=/home/runner/.local/bin:/usr/local/bin:/usr/bin:/bin bash /tmp/install-grok.sh" \
         && rm -f /tmp/install-grok.sh \
         && if [ -x /home/runner/.local/bin/grok ]; then ln -sf /home/runner/.local/bin/grok /usr/local/bin/grok; fi \
         && command -v grok >/dev/null 2>&1; \
       fi \
    && chown -R 1000:1000 /workspace /gitseed-ctl /gitseed-secrets /home/runner

COPY --from=py-build /app/.venv /app/.venv
COPY LICENSE NOTICE /app/

# Numeric UID:GID (runner=1000:1000) required by Kubernetes PodSecurity runAsNonRoot
USER 1000:1000
WORKDIR /workspace

ENTRYPOINT ["python", "-m", "gitseed_runner.main"]
