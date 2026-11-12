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


MAX_PROXY_LINE_BYTES = 8192
MAX_PROXY_HEADERS = 128
MAX_PROXY_HEADER_TOTAL_BYTES = 65536
DEFAULT_INTERNAL_ALLOWED_PORTS: frozenset[int] = frozenset({80, 443, 4000, 8000, 8420})
DEFAULT_EXTERNAL_ALLOWED_PORTS: frozenset[int] = frozenset({80, 443})


class _HeaderTooLargeError(Exception):
    """Raised when an incoming HTTP proxy request line or header block exceeds limits."""


def _split_host_and_optional_port(raw_item: str) -> tuple[str, int | None]:
    cleaned = (raw_item or "").strip()
    if not cleaned:
        return "", None
    if ":" in cleaned and not cleaned.startswith("["):
        host_part, port_part = cleaned.rsplit(":", 1)
        if port_part.isdigit():
            port_num = int(port_part)
            if 1 <= port_num <= 65535:
                return normalize_hostname(host_part), port_num
    return normalize_hostname(cleaned), None


def _parse_csv(raw: str) -> list[str]:
    out: list[str] = []
    for part in (raw or "").split(","):
        host, port = _split_host_and_optional_port(part)
        if not host:
            continue
        entry = f"{host}:{port}" if port is not None else host
        if entry not in out:
            out.append(entry)
    return out


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
        host, port = _split_host_and_optional_port(item)
        if not host:
            continue
        norm = f"{host}:{port}" if port is not None else host
        if norm not in merged:
            merged.append(norm)
    return merged


def resolve_internal_hosts(internal_raw: str | None = None) -> set[str]:
    """Return internal service names that may resolve to container-network private IPs."""
    raw = settings.EGRESS_INTERNAL_HOSTS if internal_raw is None else internal_raw
    hosts: set[str] = set()
    for part in (raw or "").split(","):
        host, _ = _split_host_and_optional_port(part)
        if host:
            hosts.add(host)
    return hosts


def _resolve_internal_host_ports(
    internal_hosts: Iterable[str] | None = None,
) -> dict[str, set[int] | None]:
    items = (
        list(internal_hosts)
        if internal_hosts is not None
        else (settings.EGRESS_INTERNAL_HOSTS or "").split(",")
    )
    mapping: dict[str, set[int] | None] = {}
    for raw_item in items:
        host, port = _split_host_and_optional_port(raw_item)
        if not host:
            continue
        if port is None:
            mapping[host] = None
        elif host not in mapping:
            mapping[host] = {port}
        elif mapping[host] is not None:
            mapping[host].add(port)  # type: ignore[union-attr]
    return mapping


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

    internal_ports_map = _resolve_internal_host_ports(internal_hosts)
    if name in internal_ports_map:
        explicit_ports = internal_ports_map[name]
        default_internal_ports = DEFAULT_INTERNAL_ALLOWED_PORTS | {
            int(getattr(settings, "API_PORT", 8420) or 8420)
        }
        allowed_ports = explicit_ports if explicit_ports is not None else default_internal_ports
        if port not in allowed_ports:
            raise EgressDeniedError(
                f"Port {port} is not permitted for internal host {name}"
            )
        try:
            addrs = await (resolver or default_resolver)(name, port)
        except (OSError, UnicodeError) as exc:
            raise EgressDeniedError(f"Could not resolve internal host {name}") from exc
        if not addrs:
            raise EgressDeniedError(f"Internal host {name} did not resolve")
        return addrs

    raw_patterns = list(allowlist) if allowlist is not None else resolve_egress_allowlist()
    host_only_patterns: list[str] = []
    explicit_external_ports: set[int] = set()
    matched_host = False
    for pat in raw_patterns:
        pat_host, pat_port = _split_host_and_optional_port(pat)
        if not pat_host:
            continue
        if host_matches(name, [pat_host]):
            matched_host = True
            if pat_port is not None:
                explicit_external_ports.add(pat_port)
            else:
                host_only_patterns.append(pat_host)
    if not matched_host:
        raise EgressDeniedError(f"Host {name} is not in the egress allowlist")

    allowed_ext_ports = set(explicit_external_ports)
    if host_only_patterns:
        allowed_ext_ports |= DEFAULT_EXTERNAL_ALLOWED_PORTS
    if port not in allowed_ext_ports:
        raise EgressDeniedError(
            f"Port {port} is not permitted for allowlisted host {name}"
        )

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


async def _read_bounded_line(
    reader: asyncio.StreamReader,
    max_bytes: int = MAX_PROXY_LINE_BYTES,
    *,
    timeout: float = 15.0,
) -> bytes:
    """Read a single CRLF/LF line bounded to ``max_bytes`` to prevent unbounded OOM buffering."""
    try:
        line = await asyncio.wait_for(reader.readuntil(b"\n"), timeout=timeout)
    except asyncio.LimitOverrunError as exc:
        raise _HeaderTooLargeError("Proxy header line exceeds maximum length") from exc
    except asyncio.IncompleteReadError as exc:
        line = exc.partial
    if len(line) > max_bytes:
        raise _HeaderTooLargeError("Proxy header line exceeds maximum length")
    return line


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
        try:
            request_line_bytes = await _read_bounded_line(client_reader)
        except _HeaderTooLargeError:
            client_writer.write(b"HTTP/1.1 414 URI Too Long\r\nConnection: close\r\n\r\n")
            await client_writer.drain()
            return
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
        total_header_bytes = len(request_line_bytes)
        host_header = ""
        try:
            while True:
                line = await _read_bounded_line(client_reader)
                if not line or line in (b"\r\n", b"\n"):
                    break
                total_header_bytes += len(line)
                if (
                    len(headers_raw) >= MAX_PROXY_HEADERS
                    or total_header_bytes > MAX_PROXY_HEADER_TOTAL_BYTES
                ):
                    raise _HeaderTooLargeError("Too many or oversized proxy headers")
                headers_raw.append(line)
                decoded = line.decode("latin-1", errors="replace")
                if decoded.lower().startswith("host:"):
                    host_header = decoded.split(":", 1)[1].strip()
        except _HeaderTooLargeError:
            client_writer.write(
                b"HTTP/1.1 431 Request Header Fields Too Large\r\nConnection: close\r\n\r\n"
            )
            await client_writer.drain()
            return

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
