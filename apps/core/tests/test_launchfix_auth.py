from __future__ import annotations

from types import SimpleNamespace

import pytest
from gitseed_common.schemas.auth import ChangePasswordRequest, LoginRequest
from gitseed_core.api.v1 import auth as auth_api
from gitseed_core.api.v1 import events as events_api
from gitseed_core.config import settings
from gitseed_core.main import _resolve_cors_origins
from gitseed_core.security import _login_rate_keys, derive_domain_hmac_key
from gitseed_core.services import session_vscode
from pydantic import ValidationError


def test_password_max_length_prevents_argon2_dos() -> None:
    with pytest.raises(ValidationError):
        LoginRequest(email="admin@gitseed.local", password="a" * 1025)
    with pytest.raises(ValidationError):
        ChangePasswordRequest(current_password="a" * 1025, new_password="ValidPassword123")


def test_login_rate_keys_binds_email_counter_to_client_ip() -> None:
    keys_ip1 = _login_rate_keys("admin@gitseed.local", "198.51.100.10")
    keys_ip2 = _login_rate_keys("admin@gitseed.local", "198.51.100.20")
    assert keys_ip1[0] != keys_ip2[0]
    assert keys_ip1[0].endswith(":198.51.100.10")
    assert keys_ip2[0].endswith(":198.51.100.20")
    assert _login_rate_keys("admin@gitseed.local", None) == [
        keys_ip1[0].rsplit(":", 1)[0]
    ]


def test_secure_cookie_and_client_ip_proxy_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "PUBLIC_WEB_URL", "http://localhost:8421")
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", False)

    fake_req = SimpleNamespace(
        url=SimpleNamespace(scheme="http"),
        headers={"x-forwarded-proto": "https", "x-forwarded-for": "203.0.113.5, 10.0.0.1"},
        client=SimpleNamespace(host="127.0.0.1"),
    )
    assert auth_api._is_secure_cookie_request(fake_req) is False  # type: ignore[arg-type]
    assert auth_api._extract_client_ip(fake_req) == "127.0.0.1"  # type: ignore[arg-type]

    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    assert auth_api._is_secure_cookie_request(fake_req) is True  # type: ignore[arg-type]
    assert auth_api._extract_client_ip(fake_req) == "203.0.113.5"  # type: ignore[arg-type]

    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", False)
    monkeypatch.setattr(settings, "PUBLIC_WEB_URL", "https://gitseed.example.com")
    assert auth_api._is_secure_cookie_request(fake_req) is True  # type: ignore[arg-type]


def test_cors_origins_strips_wildcard_and_hkdf_domain_separation() -> None:
    assert "*" not in _resolve_cors_origins("*")
    assert _resolve_cors_origins("*,https://app.example.com") == [
        "https://app.example.com"
    ]

    sse_key = derive_domain_hmac_key(b"gitseed-sse-stream-token-v1")
    vscode_key = derive_domain_hmac_key(b"gitseed-vscode-proxy-token-v1")
    assert sse_key != vscode_key
    assert sse_key != settings.GITSEED_SECRET_KEY.encode("utf-8")

    sse_tok = events_api.create_sse_stream_token("usr_123")
    assert events_api.verify_sse_stream_token(sse_tok) == "usr_123"

    vsc_tok = session_vscode.mint_vscode_token("sandbox:1", "cid_1", ttl_seconds=60)
    assert session_vscode.verify_vscode_token("sandbox:1", "cid_1", vsc_tok) is True

