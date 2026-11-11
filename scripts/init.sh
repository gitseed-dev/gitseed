#!/bin/sh
# Generate or update .env from .env.example with strong cryptographic secrets.
# Preserves any existing custom secrets in .env while replacing known weak/default placeholders.
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)"
ENV_EXAMPLE="${GITSEED_ENV_EXAMPLE:-${REPO_ROOT}/.env.example}"
ENV_FILE="${GITSEED_ENV_FILE:-${REPO_ROOT}/.env}"

rand_hex() {
    bytes="${1:-32}"
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex "$bytes"
    elif command -v python3 >/dev/null 2>&1; then
        python3 -c "import secrets; print(secrets.token_hex(${bytes}))"
    else
        dd if=/dev/urandom bs=1 count="$bytes" 2>/dev/null | od -An -tx1 | tr -d ' \n'
        echo
    fi
}

if [ ! -f "$ENV_FILE" ]; then
    if [ -f "$ENV_EXAMPLE" ]; then
        cp "$ENV_EXAMPLE" "$ENV_FILE"
    else
        : > "$ENV_FILE"
    fi
fi

chmod 600 "$ENV_FILE" 2>/dev/null || true

get_env_val() {
    key="$1"
    if [ ! -f "$ENV_FILE" ]; then
        return 0
    fi
    grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | tail -n 1 | cut -d'=' -f2- || true
}

set_env_val() {
    key="$1"
    val="$2"
    tmp_file="${ENV_FILE}.tmp.$$"
    if grep -q -E "^${key}=" "$ENV_FILE" 2>/dev/null; then
        awk -v k="$key" -v v="$val" '
            index($0, k "=") == 1 { print k "=" v; next }
            { print }
        ' "$ENV_FILE" > "$tmp_file"
        mv "$tmp_file" "$ENV_FILE"
    else
        printf '%s=%s\n' "$key" "$val" >> "$ENV_FILE"
    fi
}

is_placeholder() {
    val="$1"
    case "$val" in
        ""|"changeme"|"secret"|"password"|"admin"|"gitseed-dev-secret-key-change-in-production"|"sk-gitseed-litellm-master-key"|"sk-gitseed-litellm-salt-key"|"gitseed_password"|"gitseed_admin")
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

cur_secret="$(get_env_val GITSEED_SECRET_KEY)"
if is_placeholder "$cur_secret"; then
    set_env_val "GITSEED_SECRET_KEY" "$(rand_hex 32)"
fi

cur_master="$(get_env_val LITELLM_MASTER_KEY)"
if is_placeholder "$cur_master"; then
    set_env_val "LITELLM_MASTER_KEY" "sk-gitseed-$(rand_hex 24)"
fi

cur_salt="$(get_env_val LITELLM_SALT_KEY)"
if is_placeholder "$cur_salt"; then
    set_env_val "LITELLM_SALT_KEY" "sk-gitseed-salt-$(rand_hex 24)"
fi

cur_pg="$(get_env_val POSTGRES_PASSWORD)"
if is_placeholder "$cur_pg"; then
    new_pg="$(rand_hex 24)"
    set_env_val "POSTGRES_PASSWORD" "$new_pg"
    pg_user="$(get_env_val POSTGRES_USER)"
    pg_user="${pg_user:-gitseed}"
    pg_db="$(get_env_val POSTGRES_DB)"
    pg_db="${pg_db:-gitseed}"
    if command -v docker >/dev/null 2>&1 && [ "$(docker inspect -f '{{.State.Running}}' gitseed-postgresql 2>/dev/null || true)" = "true" ]; then
        docker exec gitseed-postgresql psql -U "$pg_user" -d "$pg_db" -c "ALTER USER \"${pg_user}\" WITH PASSWORD '${new_pg}';" >/dev/null 2>&1 || true
    fi
fi

cur_admin="$(get_env_val GITSEED_ADMIN_PASSWORD)"
if is_placeholder "$cur_admin"; then
    set_env_val "GITSEED_ADMIN_PASSWORD" "$(rand_hex 16)"
fi

chmod 600 "$ENV_FILE" 2>/dev/null || true
admin_email="$(get_env_val GITSEED_ADMIN_EMAIL)"
admin_pass="$(get_env_val GITSEED_ADMIN_PASSWORD)"
echo "Initialized ${ENV_FILE} with strong secrets."
echo "Initial Admin Login: ${admin_email:-admin@gitseed.local} / ${admin_pass}"
