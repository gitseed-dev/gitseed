from __future__ import annotations

import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class MCPSettings(BaseSettings):
    """Configuration for the GitSeed Model Context Protocol (MCP) server."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ENVIRONMENT: str = "development"
    GITSEED_VERSION: str = "dev"

    HOST: str = "127.0.0.1"
    MCP_PORT: int = 8422
    API_PORT: int = 8420

    GITSEED_API_URL: str | None = None
    RUNNER_API_URL: str | None = None

    GITSEED_API_TOKEN: str | None = None
    GITSEED_TOKEN: str | None = None
    token: str | None = None

    GITSEED_MCP_ALLOWED_ORIGINS: str = "http://localhost:8421,http://127.0.0.1:8421"
    GITSEED_MCP_TIMEOUT_SECONDS: float = 30.0
    GITSEED_MCP_TOOLSETS: str = ""
    GITSEED_MCP_READ_ONLY: bool = False

    @property
    def api_base_url(self) -> str:
        """Resolved Control Plane API base URL without trailing slash."""
        raw = (
            self.GITSEED_API_URL
            or os.getenv("GITSEED_API_URL")
            or self.RUNNER_API_URL
            or os.getenv("RUNNER_API_URL")
            or f"http://localhost:{self.API_PORT}"
        )
        return raw.rstrip("/")

    @property
    def allowed_origins(self) -> tuple[str, ...]:
        """Normalized tuple of Origin header values permitted on HTTP/SSE transports."""
        if (
            "GITSEED_MCP_ALLOWED_ORIGINS" in self.model_fields_set
            and self.GITSEED_MCP_ALLOWED_ORIGINS.strip()
        ):
            sources = [self.GITSEED_MCP_ALLOWED_ORIGINS]
        elif os.getenv("GITSEED_MCP_ALLOWED_ORIGINS", "").strip():
            sources = [os.environ["GITSEED_MCP_ALLOWED_ORIGINS"]]
        else:
            sources = [
                self.GITSEED_MCP_ALLOWED_ORIGINS,
                os.getenv("ALLOWED_ORIGINS", ""),
                os.getenv("CORS_ORIGINS", ""),
                os.getenv("PUBLIC_WEB_URL", ""),
            ]
        seen: dict[str, None] = {}
        for source in sources:
            for item in source.split(","):
                cleaned = item.strip().rstrip("/")
                if cleaned and cleaned not in seen:
                    seen[cleaned] = None
        return tuple(seen.keys())

    @property
    def enabled_toolsets(self) -> frozenset[str]:
        """Normalized set of enabled toolset names (empty frozenset means all toolsets)."""
        if "GITSEED_MCP_TOOLSETS" in self.model_fields_set:
            raw = self.GITSEED_MCP_TOOLSETS
        else:
            raw = os.getenv("GITSEED_MCP_TOOLSETS", self.GITSEED_MCP_TOOLSETS)
        parsed = frozenset(
            part.strip().lower() for part in raw.split(",") if part.strip()
        )
        if not parsed or "*" in parsed or "all" in parsed:
            return frozenset()
        return parsed

    @property
    def read_only(self) -> bool:
        """Return True when mutating MCP tools are disabled."""
        if "GITSEED_MCP_READ_ONLY" in self.model_fields_set:
            return bool(self.GITSEED_MCP_READ_ONLY)
        env_val = os.getenv("GITSEED_MCP_READ_ONLY")
        if env_val is not None and env_val.strip():
            return env_val.strip().lower() in ("1", "true", "yes", "on")
        return bool(self.GITSEED_MCP_READ_ONLY)

    def is_origin_allowed(self, origin: str) -> bool:
        """Return True if the browser Origin header value is in the configured allowlist."""
        normalized = origin.strip().rstrip("/")
        if not normalized:
            return False
        allowed = self.allowed_origins
        if "*" in allowed:
            return True
        return normalized.lower() in {item.lower() for item in allowed}

    def resolve_token(
        self,
        request_token: str | None = None,
        *,
        allow_env_fallback: bool = True,
    ) -> str | None:
        """Resolve caller API token from request header or (on stdio only) environment default."""
        if request_token and request_token.strip():
            return request_token.strip()
        if not allow_env_fallback:
            return None
        env_token = (
            self.token
            or self.GITSEED_API_TOKEN
            or os.getenv("GITSEED_API_TOKEN")
            or self.GITSEED_TOKEN
            or os.getenv("GITSEED_TOKEN")
        )
        if env_token and env_token.strip():
            return env_token.strip()
        return None


MCPConfig = MCPSettings


@lru_cache
def get_settings() -> MCPSettings:
    return MCPSettings()

