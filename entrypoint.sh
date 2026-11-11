#!/bin/sh
# GitSeed control plane entrypoint
set -eu

VERSION="${GITSEED_VERSION:-dev}"
HOST="${HOST:-0.0.0.0}"
API_PORT="${API_PORT:-8420}"
WEB_PORT="${WEB_PORT:-8421}"
MCP_PORT="${MCP_PORT:-8422}"
SANDBOX_PORT="${SANDBOX_PORT:-8423}"
AUTO_MIGRATE="${GITSEED_AUTO_MIGRATE:-true}"

banner() {
  cat <<'EOF'
   ____ _ _   ____                _
  / ___(_) |_/ ___|  ___  ___  __| |
 | |  _| | __\___ \ / _ \/ _ \/ _` |
 | |_| | | |_ ___) |  __/  __/ (_| |
  \____|_|\__|____/ \___|\___|\__,_|

EOF
  echo "  v${VERSION} · $1"
  echo
}

usage() {
  cat <<EOF
GitSeed v${VERSION}

Usage: docker run [options] gitseed/gitseed <command> [args...]

Commands:
  api          FastAPI server                          · port \$API_PORT (${API_PORT})
  web          Web app (static UI)                     · port \$WEB_PORT (${WEB_PORT})
  scheduler    Launches and supervises runner containers, runs background jobs
  egress-proxy Allowlisting HTTP CONNECT egress proxy for runners and sandboxes
  mcp          MCP server                              · port \$MCP_PORT (${MCP_PORT})
  cli          GitSeed CLI (e.g. cli get users)
  migrate      Apply database migrations and exit
  help         Show this message

Any other executable in the image can be run directly, for example:
  gitseed get users
  alembic current
  sh
EOF
}

CMD="${1:-}"
[ "$#" -gt 0 ] && shift

case "$CMD" in
  api)
    banner "Control Plane API on ${HOST}:${API_PORT}"
    mkdir -p "${ARTIFACTS_DIR:-/app/data/artifacts}" 2>/dev/null || true
    if [ "$AUTO_MIGRATE" = "true" ]; then
      alembic upgrade head
    fi
    exec uvicorn gitseed_core.main:app \
         --host "$HOST" --port "$API_PORT" --proxy-headers "$@"
    ;;
  web)
    banner "Web app on ${HOST}:${WEB_PORT} (sandbox origin :${SANDBOX_PORT})"
    export SITE_ADDRESS="${SITE_ADDRESS:-:${WEB_PORT}}"
    export SANDBOX_SITE_ADDRESS="${SANDBOX_SITE_ADDRESS:-:${SANDBOX_PORT:-8423}}"
    export API_UPSTREAM="${API_UPSTREAM:-api:${API_PORT}}"
    exec caddy run --config /app/Caddyfile --adapter caddyfile "$@"
    ;;
  scheduler)
    banner "Scheduler"
    mkdir -p "${ARTIFACTS_DIR:-/app/data/artifacts}" 2>/dev/null || true
    exec python -m gitseed_core.main "$@"
    ;;
  egress-proxy)
    banner "Egress Proxy"
    # Egress proxy runs without control-plane secrets; satisfy Settings() fail-closed startup check if unset.
    if [ -z "${GITSEED_SECRET_KEY:-}" ]; then
      GITSEED_SECRET_KEY="ephemeral-egress-unused-$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')"
      export GITSEED_SECRET_KEY
    fi
    if [ -z "${LITELLM_MASTER_KEY:-}" ]; then
      LITELLM_MASTER_KEY="ephemeral-egress-unused-$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"
      export LITELLM_MASTER_KEY
    fi
    if [ -z "${POSTGRES_PASSWORD:-}" ]; then
      POSTGRES_PASSWORD="ephemeral-egress-unused-$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"
      export POSTGRES_PASSWORD
    fi
    exec python -m gitseed_core.main egress-proxy "$@"
    ;;
  mcp)
    banner "MCP server on ${HOST}:${MCP_PORT}"
    exec python -m gitseed_mcp.main --host "$HOST" --port "$MCP_PORT" "$@"
    ;;
  cli)
    exec gitseed "$@"
    ;;
  migrate)
    banner "migrations"
    exec alembic upgrade head "$@"
    ;;
  help|-h|--help)
    usage
    exit 0
    ;;
  "")
    echo "error: no command given" >&2
    echo >&2
    usage >&2
    exit 2
    ;;
  *)
    # Pass through any real executable without the banner,
    # so its output stays clean for scripts.
    if command -v "$CMD" >/dev/null 2>&1; then
      exec "$CMD" "$@"
    fi
    echo "error: unknown command '$CMD'" >&2
    echo >&2
    usage >&2
    exit 2
    ;;
esac