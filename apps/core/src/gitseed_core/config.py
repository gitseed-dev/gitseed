import logging
from typing import Any
from urllib.parse import quote

from pydantic import computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_DEV_SECRET_KEY = "gitseed-dev-secret-key-change-in-production"
_KNOWN_INSECURE_SECRET_KEYS = frozenset(
    {
        _DEFAULT_DEV_SECRET_KEY,
        "gitseed-k8s-secret-key-change-in-production-32chars",
    }
)
_DEFAULT_LITELLM_MASTER_KEY = "sk-gitseed-litellm-master-key"
_DEFAULT_LITELLM_SALT_KEY = "sk-gitseed-litellm-salt-key"
_DEFAULT_POSTGRES_PASSWORD = "gitseed_password"
# Values that shipped as defaults in compose/.env.example/Helm or are trivially guessable.
_KNOWN_WEAK_VALUES = frozenset(
    {
        _DEFAULT_LITELLM_MASTER_KEY,
        _DEFAULT_LITELLM_SALT_KEY,
        _DEFAULT_POSTGRES_PASSWORD,
        "postgres",
        "password",
        "changeme",
        "change-me",
        "secret",
        "gitseed",
    }
)
_MIN_SECRET_KEY_LENGTH = 32
_MIN_SERVICE_SECRET_LENGTH = 16


def insecure_secret_problems(cfg: Any) -> list[str]:
    """Return human-readable problems for default/weak secrets in ``cfg`` (empty if OK)."""
    problems: list[str] = []
    secret_key = str(cfg.GITSEED_SECRET_KEY or "").strip()
    if secret_key in _KNOWN_INSECURE_SECRET_KEYS or len(secret_key) < _MIN_SECRET_KEY_LENGTH:
        problems.append(
            f"GITSEED_SECRET_KEY must be a unique secret of at least {_MIN_SECRET_KEY_LENGTH} characters"
        )
    for raw_prev in str(cfg.GITSEED_SECRET_PREVIOUS_KEYS or "").split(","):
        prev = raw_prev.strip()
        if prev and (prev in _KNOWN_INSECURE_SECRET_KEYS or len(prev) < _MIN_SECRET_KEY_LENGTH):
            problems.append(
                "Each key in GITSEED_SECRET_PREVIOUS_KEYS must be a unique secret of at least "
                f"{_MIN_SECRET_KEY_LENGTH} characters"
            )
            break
    receipt_key = str(getattr(cfg, "GITSEED_RECEIPT_SIGNING_KEY", "") or "").strip()
    if receipt_key and (
        receipt_key in _KNOWN_INSECURE_SECRET_KEYS or len(receipt_key) < _MIN_SECRET_KEY_LENGTH
    ):
        problems.append(
            "GITSEED_RECEIPT_SIGNING_KEY must be a unique secret of at least "
            f"{_MIN_SECRET_KEY_LENGTH} characters"
        )
    for raw_rcpt_prev in str(getattr(cfg, "GITSEED_RECEIPT_PREVIOUS_SIGNING_KEYS", "") or "").split(","):
        rcpt_prev = raw_rcpt_prev.strip()
        if rcpt_prev and (
            rcpt_prev in _KNOWN_INSECURE_SECRET_KEYS or len(rcpt_prev) < _MIN_SECRET_KEY_LENGTH
        ):
            problems.append(
                "Each key in GITSEED_RECEIPT_PREVIOUS_SIGNING_KEYS must be a unique secret of at least "
                f"{_MIN_SECRET_KEY_LENGTH} characters"
            )
            break
    service_secrets = {
        "LITELLM_MASTER_KEY": (str(cfg.LITELLM_MASTER_KEY or "").strip(), True),
        # The salt key is only consumed by LiteLLM itself; validate it when present.
        "LITELLM_SALT_KEY": (str(cfg.LITELLM_SALT_KEY or "").strip(), False),
        "POSTGRES_PASSWORD": (str(cfg.POSTGRES_PASSWORD or "").strip(), True),
    }
    for name, (value, required) in service_secrets.items():
        if not value:
            if required:
                problems.append(f"{name} must be set")
            continue
        if value.lower() in _KNOWN_WEAK_VALUES or len(value) < _MIN_SERVICE_SECRET_LENGTH:
            problems.append(
                f"{name} uses a known default or is shorter than {_MIN_SERVICE_SECRET_LENGTH} characters"
            )
    return problems


class Settings(BaseSettings):
    """GitSeed Core Application Configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application
    APP_NAME: str = "GitSeed Control Plane"
    GITSEED_VERSION: str = "dev"
    ENVIRONMENT: str = "development"
    DEBUG: bool = False
    GITSEED_AUTO_MIGRATE: bool = True

    # Bootstrap Admin & Session Auth
    GITSEED_ADMIN_EMAIL: str = "admin@gitseed.local"
    GITSEED_ADMIN_NAME: str = "Admin"
    GITSEED_ADMIN_PASSWORD: str | None = None
    GITSEED_SECRET_KEY: str = _DEFAULT_DEV_SECRET_KEY
    # Optional comma-separated list of retired secret keys for zero-downtime MultiFernet rotation
    GITSEED_SECRET_PREVIOUS_KEYS: str = ""
    # Optional dedicated secret key for Ed25519 Governance Receipt signing (defaults to GITSEED_SECRET_KEY)
    GITSEED_RECEIPT_SIGNING_KEY: str = ""
    # Optional comma-separated list of retired dedicated receipt signing keys for zero-downtime rotation
    GITSEED_RECEIPT_PREVIOUS_SIGNING_KEYS: str = ""
    SESSION_COOKIE_NAME: str = "gitseed_session"
    SESSION_TTL_SECONDS: int = 604800  # 7 days

    # Escape hatch for unit tests / throwaway local experiments ONLY: when true, startup
    # tolerates the well-known default secrets below and logs a loud warning instead.
    GITSEED_ALLOW_INSECURE_DEFAULTS: bool = False

    @model_validator(mode="after")
    def _validate_secrets_fail_closed(self) -> "Settings":
        problems = insecure_secret_problems(self)
        if not problems:
            return self
        if self.GITSEED_ALLOW_INSECURE_DEFAULTS and self.ENVIRONMENT.strip().lower() != "production":
            logging.getLogger("gitseed.config").warning(
                "\n%s\n  INSECURE CONFIGURATION (GITSEED_ALLOW_INSECURE_DEFAULTS=true)\n%s\n%s",
                "!" * 70,
                "\n".join(f"  - {p}" for p in problems),
                "!" * 70,
            )
            return self
        raise ValueError(
            "Refusing to start with default or weak secrets:\n"
            + "\n".join(f"  - {p}" for p in problems)
            + "\nRun `make init` (or ./scripts/init.sh) to generate strong secrets in .env."
        )

    # Network & Ports
    HOST: str = "0.0.0.0"
    API_PORT: int = 8420
    WEB_PORT: int = 8421
    MCP_PORT: int = 8422
    PUBLIC_WEB_URL: str = "http://localhost:8421"
    CORS_ORIGINS: str = "http://localhost:8421,http://127.0.0.1:8421"
    # Comma-separated origins allowed for cookie-authenticated WebSockets / state-changing
    # requests. Empty = PUBLIC_WEB_URL + CORS_ORIGINS.
    ALLOWED_ORIGINS: str = ""
    # Dedicated origin (scheme://host[:port]) that serves the interactive VS Code proxy, kept
    # separate from the API/web origin so sandbox content cannot read GitSeed cookies.
    # Empty = serve on the API origin (single-origin dev setups; blocked in production unless
    # ALLOW_SAME_ORIGIN_VSCODE=true).
    SANDBOX_ORIGIN: str = ""
    ALLOW_SAME_ORIGIN_VSCODE: bool = False
    # Trust X-Forwarded-Host / X-Forwarded-Proto headers only when behind a trusted reverse proxy
    # that overwrites client-supplied forwarding headers.
    TRUST_PROXY_HEADERS: bool = False

    # Local git (bring-your-own workspace): comma-separated directory roots a project's
    # workspace_path must live under. Empty = the user's home directory.
    LOCAL_WORKSPACE_ROOTS: str = ""

    # Runner egress: allowlisting HTTP(S) proxy that runner/sandbox containers must use.
    RUNNER_EGRESS_PROXY_URL: str = ""
    # Comma-separated hostnames / *.suffix wildcards; empty = built-in defaults
    # (model providers + package registries). EGRESS_ALLOWLIST_EXTRA appends to either.
    EGRESS_ALLOWLIST: str = ""
    EGRESS_ALLOWLIST_EXTRA: str = ""
    # Internal service names the egress proxy may reach despite private IPs.
    EGRESS_INTERNAL_HOSTS: str = "litellm,api"
    EGRESS_PROXY_PORT: int = 3128

    # Interactive sandboxes: opt-in host.docker.internal mapping (reach host services).
    SANDBOX_ALLOW_HOST_GATEWAY: bool = False

    # Personal Mode (subscription CLI OAuth): enable/disable instance-wide and restrict to single-owner Seeds by default.
    GITSEED_ALLOW_PERSONAL_CLI_OAUTH: bool = True
    ALLOW_PERSONAL_MODE_IN_MULTI_USER_ORGS: bool = False

    # Scheduler, LiteLLM Proxy & Runner Execution
    GITSEED_EMBED_SCHEDULER: bool = False
    SCHEDULER_POLL_SECONDS: float = 3.0
    LITELLM_URL: str = "http://litellm:4000"
    LITELLM_BASE_URL: str = ""
    LITELLM_MASTER_KEY: str = _DEFAULT_LITELLM_MASTER_KEY
    LITELLM_SALT_KEY: str = ""
    RUNNER_IMAGE: str = "gitseed/runner:dev"
    # Optional comma-separated list of agent runtimes installed in RUNNER_IMAGE
    # (e.g. "opencode,codex,gemini,claude-code"). When empty, Docker backends auto-probe the image.
    RUNNER_RUNTIMES: str = ""
    RUNNER_DOCKER_NETWORK: str = "gitseed-runners"
    RUNNER_API_URL: str = "http://api:8420"
    RUNNER_GATEWAY_URL: str = "http://litellm:4000/v1"
    RUNNER_MAX_TURNS: int = 50
    RUNNER_MAX_FILE_BYTES: int = 5 * 1024 * 1024
    RUNNER_MAX_DIFF_BYTES: int = 20 * 1024 * 1024
    RUNNER_BLOCKED_PATHS_ACTION: str = "fail"  # "fail" | "revert"

    # Bootstrap Compute Backend & Kubernetes Runtime
    GITSEED_BOOTSTRAP_COMPUTE_PROVIDER: str = "auto"  # "auto" | "docker" | "kubernetes" | "none"
    DOCKER_HOST: str = "unix:///var/run/docker.sock"
    K8S_API_URL: str = "https://kubernetes.default.svc"
    CONTROL_PLANE_K8S_NAMESPACE: str = ""
    RUNNER_K8S_NAMESPACE: str = ""
    SANDBOX_K8S_NAMESPACE: str = ""
    K8S_NAMESPACE_ISOLATION: str = "workload"  # "shared" | "workload" | "project"
    RUNNER_K8S_SERVICE_ACCOUNT: str = ""

    # Project Sandbox & Artifacts
    ARTIFACTS_DIR: str = "/app/data/artifacts"
    SANDBOX_DOCKER_NETWORK: str = "gitseed-sandboxes"
    SANDBOX_ALLOWED_IMAGE_PREFIXES: str = ""
    SANDBOX_DEFAULT_TTL_MINUTES: int = 15
    SANDBOX_ARTIFACT_RETENTION_DAYS: int = 7
    SANDBOX_MAX_ARTIFACT_BYTES: int = 500 * 1024 * 1024

    # Interactive sessions (terminals & agent chats): org-wide limits
    SESSION_IDLE_MINUTES: int = 30
    SESSION_MAX_HOURS: int = 4

    # PostgreSQL
    POSTGRES_HOST: str = "localhost"
    POSTGRES_USER: str = "gitseed"
    POSTGRES_PASSWORD: str = _DEFAULT_POSTGRES_PASSWORD
    POSTGRES_DB: str = "gitseed"
    POSTGRES_PORT: int = 5432
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 30
    DB_POOL_TIMEOUT: int = 30
    DB_POOL_RECYCLE: int = 1800

    # Valkey (Redis-compatible)
    VALKEY_PORT: int = 6379
    REDIS_URL: str = "redis://localhost:6379/0"

    # Optional Mobile Push Relay (APNs / FCM bridge for GitSeed Mobile & Wearable clients)
    MOBILE_PUSH_RELAY_URL: str = ""
    MOBILE_PUSH_RELAY_SECRET: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def DATABASE_URL(self) -> str:
        user = quote(self.POSTGRES_USER, safe="")
        password = quote(self.POSTGRES_PASSWORD, safe="")
        return (
            f"postgresql+psycopg://{user}:{password}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def DATABASE_URL_SYNC(self) -> str:
        user = quote(self.POSTGRES_USER, safe="")
        password = quote(self.POSTGRES_PASSWORD, safe="")
        return (
            f"postgresql+psycopg://{user}:{password}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )


settings = Settings()
