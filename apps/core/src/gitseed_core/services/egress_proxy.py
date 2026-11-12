"""Allowlisting HTTP/HTTPS CONNECT egress proxy for isolated runner and sandbox networks."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Iterable
from urllib.parse import urlsplit

from gitseed_core.config import settings
from gitseed_core.services.net_safety import (
    Resolver,
    UnsafeDestinationError,
    default_resolver,
    host_matches,
    normalize_hostname,
    resolve_public_ips,
)

logger = logging.getLogger("uvicorn.error")

DEFAULT_EGRESS_ALLOWLIST: tuple[str, ...] = (
    # Model providers & subscription auth
    "api.anthropic.com",
    "*.anthropic.com",
    "claude.ai",
    "*.claude.ai",
    "api.openai.com",
    "*.openai.com",
    "chatgpt.com",
    "*.chatgpt.com",
    "auth.openai.com",
    "generativelanguage.googleapis.com",
    "*.googleapis.com",
    "oauth2.googleapis.com",
    "accounts.google.com",
    "*.google.com",
    "antigravity.google",
    "*.antigravity.google",
    "api.x.ai",
    "*.x.ai",
    "opencode.ai",
    "*.opencode.ai",
    "models.dev",
    "*.models.dev",
    # Package registries & Git hosting
    "registry.npmjs.org",
    "pypi.org",
    "files.pythonhosted.org",
    "proxy.golang.org",
    "crates.io",
    "static.crates.io",
    "github.com",
    "*.github.com",
    "*.githubusercontent.com",
    "gitlab.com",
    "*.gitlab.com",
    "bitbucket.org",
    "*.bitbucket.org",
)


class EgressDeniedError(UnsafeDestinationError):
    """Raised when an outbound connection via the egress proxy is blocked."""


def _parse_csv(raw: str) -> list[str]:
    return [normalize_hostname(part) for part in (raw or "").split(",") if normalize_hostname(part)]


def resolve_egress_allowlist(
    allowlist_raw: str | None = None,
    extra_raw: str | None = None,
) -> list[str]:
    """Return the effective hostname allowlist from settings or explicit overrides."""
    raw_base = settings.EGRESS_ALLOWLIST if allowlist_raw is None else allowlist_raw
    raw_extra = settings.EGRESS_ALLOWLIST_EXTRA if extra_raw is None else extra_raw
    base = _parse_csv(raw_base) or list(DEFAULT_EGRESS_ALLOWLIST)
    extra = _parse_csv(raw_extra)
    merged: list[str] = []
    for item in (*base, *extra):
        norm = normalize_hostname(item)
        if norm and norm not in merged:
            merged.append(norm)
    return merged


def resolve_internal_hosts(internal_raw: str | None = None) -> set[str]:
    """Return internal service names that may resolve to container-network private IPs."""
    raw = settings.EGRESS_INTERNAL_HOSTS if internal_raw is None else internal_raw
    return set(_parse_csv(raw))


async def check_egress_destination(
    host: str,
    port: int,
    *,
    allowlist: Iterable[str] | None = None,
    internal_hosts: Iterable[str] | None = None,
    resolver: Resolver | None = None,
) -> list[str]:
    """Validate `(host, port)` against the egress allowlist and public-IP policy.

    Returns the resolved IP addresses to connect to directly (preventing DNS rebinding).
    Raises :class:`EgressDeniedError` (a subclass of :class:`UnsafeDestinationError`) when
    denied.
    """
    name = normalize_hostname(host)
    if not name:
        raise EgressDeniedError("Egress destination host is empty")
    if port <= 0 or port > 65535:
        raise EgressDeniedError(f"Egress destination port {port} is invalid")

    internals = (
        {normalize_hostname(h) for h in internal_hosts if normalize_hostname(h)}
        if internal_hosts is not None
        else resolve_internal_hosts()
    )
    if name in internals:
        try:
            addrs = await (resolver or default_resolver)(name, port)
        except (OSError, UnicodeError) as exc:
            raise EgressDeniedError(f"Could not resolve internal host {name}") from exc
        if not addrs:
            raise EgressDeniedError(f"Internal host {name} did not resolve")
        return addrs

    patterns = list(allowlist) if allowlist is not None else resolve_egress_allowlist()
    if not host_matches(name, patterns):
        raise EgressDeniedError(f"Host {name} is not in the egress allowlist")

    try:
        return await resolve_public_ips(name, port, resolver=resolver)
    except UnsafeDestinationError as exc:
        raise EgressDeniedError(str(exc)) from exc


async def _pipe_streams(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except Exception:
        pass
    finally:
        with contextlib.suppress(Exception):
            if writer.can_write_eof():
                writer.write_eof()


def _parse_connect_authority(target: str) -> tuple[str, int]:
    cleaned = target.strip()
    if cleaned.startswith("["):
        bracket_end = cleaned.find("]")
        if bracket_end == -1:
            raise EgressDeniedError("Invalid IPv6 authority in CONNECT")
        host = cleaned[1:bracket_end]
        rest = cleaned[bracket_end + 1 :]
        if rest.startswith(":"):
            port = int(rest[1:])
        else:
            port = 443
        return host, port
    if ":" in cleaned:
        host, port_s = cleaned.rsplit(":", 1)
        return host, int(port_s)
    return cleaned, 443


async def handle_proxy_client(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    *,
    allowlist: Iterable[str] | None = None,
    internal_hosts: Iterable[str] | None = None,
    resolver: Resolver | None = None,
    connect_timeout: float = 10.0,
) -> None:
    """Handle a single HTTP proxy or HTTP CONNECT client connection."""
    upstream_writer: asyncio.StreamWriter | None = None
    try:
        request_line_bytes = await asyncio.wait_for(client_reader.readline(), timeout=15.0)
        if not request_line_bytes:
            return
        request_line = request_line_bytes.decode("latin-1", errors="replace").strip()
        parts = request_line.split(" ")
        if len(parts) < 2:
            client_writer.write(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n")
            await client_writer.drain()
            return

        method = parts[0].upper()
        target = parts[1]
        version = parts[2] if len(parts) > 2 else "HTTP/1.1"

        headers_raw: list[bytes] = []
        host_header = ""
        while True:
            line = await asyncio.wait_for(client_reader.readline(), timeout=15.0)
            if not line or line in (b"\r\n", b"\n"):
                break
            headers_raw.append(line)
            decoded = line.decode("latin-1", errors="replace")
            if decoded.lower().startswith("host:"):
                host_header = decoded.split(":", 1)[1].strip()

        if method == "CONNECT":
            try:
                host, port = _parse_connect_authority(target)
                addrs = await check_egress_destination(
                    host,
                    port,
                    allowlist=allowlist,
                    internal_hosts=internal_hosts,
                    resolver=resolver,
                )
            except (ValueError, EgressDeniedError) as exc:
                msg = str(exc).encode("utf-8", errors="replace")
                client_writer.write(
                    b"HTTP/1.1 403 Forbidden\r\n"
                    b"Content-Type: text/plain; charset=utf-8\r\n"
                    b"Connection: close\r\n"
                    + f"Content-Length: {len(msg)}\r\n\r\n".encode("ascii")
                    + msg
                )
                await client_writer.drain()
                return

            try:
                upstream_reader, upstream_writer = await asyncio.wait_for(
                    asyncio.open_connection(addrs[0], port),
                    timeout=connect_timeout,
                )
            except Exception:
                client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
                await client_writer.drain()
                return

            client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await client_writer.drain()
            await asyncio.gather(
                _pipe_streams(client_reader, upstream_writer),
                _pipe_streams(upstream_reader, client_writer),
            )
            return

        # Plain HTTP proxy request (e.g. GET http://host:port/path HTTP/1.1)
        try:
            if target.startswith(("http://", "https://")):
                parsed = urlsplit(target)
                if parsed.scheme.lower() != "http":
                    raise EgressDeniedError("Plain HTTP proxy only supports http:// URLs; use CONNECT for HTTPS")
                host = parsed.hostname or ""
                port = parsed.port or 80
                path = parsed.path or "/"
                if parsed.query:
                    path = f"{path}?{parsed.query}"
            else:
                if not host_header:
                    raise EgressDeniedError("Missing Host header in proxy request")
                host, port = _parse_connect_authority(host_header)
                if ":" not in host_header:
                    port = 80
                path = target
            addrs = await check_egress_destination(
                host,
                port,
                allowlist=allowlist,
                internal_hosts=internal_hosts,
                resolver=resolver,
            )
        except (ValueError, EgressDeniedError) as exc:
            msg = str(exc).encode("utf-8", errors="replace")
            client_writer.write(
                b"HTTP/1.1 403 Forbidden\r\n"
                b"Content-Type: text/plain; charset=utf-8\r\n"
                b"Connection: close\r\n"
                + f"Content-Length: {len(msg)}\r\n\r\n".encode("ascii")
                + msg
            )
            await client_writer.drain()
            return

        try:
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(addrs[0], port),
                timeout=connect_timeout,
            )
        except Exception:
            client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
            await client_writer.drain()
            return

        upstream_writer.write(f"{method} {path} {version}\r\n".encode("latin-1"))
        for hdr in headers_raw:
            lower = hdr.decode("latin-1", errors="replace").lower()
            if lower.startswith(("proxy-connection:", "proxy-authorization:")):
                continue
            upstream_writer.write(hdr)
        upstream_writer.write(b"Connection: close\r\n\r\n")
        await upstream_writer.drain()

        await asyncio.gather(
            _pipe_streams(client_reader, upstream_writer),
            _pipe_streams(upstream_reader, client_writer),
        )
    except Exception:
        pass
    finally:
        if upstream_writer is not None:
            with contextlib.suppress(Exception):
                upstream_writer.close()
                await upstream_writer.wait_closed()
        with contextlib.suppress(Exception):
            client_writer.close()
            await client_writer.wait_closed()


async def start_egress_proxy_server(
    host: str = "0.0.0.0",
    port: int | None = None,
    *,
    allowlist: Iterable[str] | None = None,
    internal_hosts: Iterable[str] | None = None,
    resolver: Resolver | None = None,
) -> asyncio.AbstractServer:
    """Start the allowlisting HTTP CONNECT egress proxy server."""
    effective_port = settings.EGRESS_PROXY_PORT if port is None else port
    return await asyncio.start_server(
        lambda r, w: handle_proxy_client(
            r,
            w,
            allowlist=allowlist,
            internal_hosts=internal_hosts,
            resolver=resolver,
        ),
        host=host,
        port=effective_port,
    )


async def run_egress_proxy_forever(
    stop_event: asyncio.Event | None = None,
    *,
    host: str | None = None,
    port: int | None = None,
) -> None:
    """Run the egress proxy server until ``stop_event`` is set or cancelled."""
    bind_host = host or settings.HOST
    bind_port = settings.EGRESS_PROXY_PORT if port is None else port
    server = await start_egress_proxy_server(host=bind_host, port=bind_port)
    logger.info("Egress proxy listening on %s:%s", bind_host, bind_port)
    async with server:
        if stop_event is not None:
            await stop_event.wait()
        else:
            await server.serve_forever()
