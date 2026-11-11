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
        if "GITSEED_MCP_ALLOWED_ORIGINS" in self.model_fields_set:
            source = self.GITSEED_MCP_ALLOWED_ORIGINS
        else:
            source = os.getenv(
                "GITSEED_MCP_ALLOWED_ORIGINS", self.GITSEED_MCP_ALLOWED_ORIGINS
            )
        return tuple(
            item.strip().rstrip("/") for item in source.split(",") if item.strip()
        )

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

