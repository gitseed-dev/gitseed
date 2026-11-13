"""Shared network-destination safety checks (SSRF / egress guards).

Used by outbound webhooks and the runner egress proxy to refuse connections to
private, loopback, link-local (cloud metadata) and other non-public addresses
after DNS resolution, so a public-looking hostname that resolves to an internal
address is still rejected.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Iterable
from urllib.parse import urlsplit

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

# Resolver signature: (host, port) -> list of IP address strings.
Resolver = Callable[[str, int], Awaitable[list[str]]]

# Explicit deny ranges. Most are also covered by ``ip.is_global`` being False, but
# listing them keeps the policy auditable and independent of Python version quirks.
_BLOCKED_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",  # carrier-grade NAT
        "127.0.0.0/8",
        "169.254.0.0/16",  # link-local, incl. cloud metadata 169.254.169.254
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "::/128",
        "::1/128",
        "64:ff9b::/96",  # RFC 6052 NAT64 well-known prefix
        "64:ff9b:1::/48",  # RFC 8215 NAT64 local-use prefix
        "fc00::/7",  # unique local (incl. fd00:ec2::254 metadata)
        "fe80::/10",
        "ff00::/8",
    )
)


class UnsafeDestinationError(ValueError):
    """Raised when a destination host/URL resolves to a non-public address."""


_NAT64_WELL_KNOWN = ipaddress.IPv6Network("64:ff9b::/96")


def _normalize_ip(ip: IPAddress) -> IPAddress:
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped or ip.sixtofour or ip.teredo and ip.teredo[1]
        if mapped is not None:
            return mapped
        if ip in _NAT64_WELL_KNOWN:
            return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        ip_int = int(ip)
        if ip_int > 1 and (ip_int >> 32) == 0:
            # Deprecated IPv4-compatible IPv6 address (::a.b.c.d)
            return ipaddress.IPv4Address(ip_int & 0xFFFFFFFF)
    return ip


def is_public_ip(value: str | IPAddress) -> bool:
    """Return True only for globally routable unicast addresses."""
    try:
        ip = ipaddress.ip_address(value) if isinstance(value, str) else value
    except ValueError:
        return False
    ip = _normalize_ip(ip)
    if any(ip in net for net in _BLOCKED_NETWORKS if net.version == ip.version):
        return False
    return ip.is_global and not ip.is_multicast


async def default_resolver(host: str, port: int) -> list[str]:
    """Resolve ``host`` to a de-duplicated list of IP strings using the event loop."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    seen: list[str] = []
    for _family, _type, _proto, _canon, sockaddr in infos:
        addr = str(sockaddr[0])
        if addr not in seen:
            seen.append(addr)
    return seen


def normalize_hostname(host: str) -> str:
    """Lower-case a hostname and strip brackets / a trailing dot."""
    return host.strip().strip("[]").rstrip(".").lower()


def host_matches(host: str, patterns: Iterable[str]) -> bool:
    """Match ``host`` against exact names and ``*.suffix`` wildcard patterns.

    ``*.example.com`` matches ``api.example.com`` and ``a.b.example.com`` but not
    ``example.com`` itself (list it explicitly if wanted).
    """
    name = normalize_hostname(host)
    if not name:
        return False
    for raw in patterns:
        pattern = normalize_hostname(raw)
        if not pattern:
            continue
        if pattern.startswith("*."):
            suffix = pattern[1:]  # ".example.com"
            if name.endswith(suffix) and len(name) > len(suffix):
                return True
        elif name == pattern:
            return True
    return False


async def resolve_public_ips(
    host: str,
    port: int,
    *,
    resolver: Resolver | None = None,
) -> list[str]:
    """Resolve ``host`` and require that EVERY resulting address is public.

    Returns the resolved addresses so callers can pin the connection to them
    (avoiding DNS-rebinding between check and use). Raises
    :class:`UnsafeDestinationError` otherwise.
    """
    name = normalize_hostname(host)
    if not name:
        raise UnsafeDestinationError("Destination host is empty")
    try:
        literal = ipaddress.ip_address(name)
    except ValueError:
        literal = None
    if literal is not None:
        if not is_public_ip(literal):
            raise UnsafeDestinationError(f"Destination address {name} is not public")
        return [str(literal)]
    if name == "localhost" or name.endswith((".localhost", ".local", ".internal")):
        raise UnsafeDestinationError(f"Destination host {name} is internal")
    try:
        addresses = await (resolver or default_resolver)(name, port)
    except (OSError, UnicodeError) as exc:
        raise UnsafeDestinationError(f"Could not resolve destination host {name}") from exc
    if not addresses:
        raise UnsafeDestinationError(f"Destination host {name} did not resolve")
    blocked = [addr for addr in addresses if not is_public_ip(addr)]
    if blocked:
        raise UnsafeDestinationError(
            f"Destination host {name} resolves to non-public address {blocked[0]}"
        )
    return addresses


async def validate_public_http_url(
    url: str,
    *,
    resolver: Resolver | None = None,
    allowed_schemes: tuple[str, ...] = ("https", "http"),
) -> list[str]:
    """Validate an outbound http(s) URL: scheme, no credentials, public resolution."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError as exc:
        raise UnsafeDestinationError("Destination URL is malformed") from exc
    if parts.scheme.lower() not in allowed_schemes:
        raise UnsafeDestinationError("Destination URL must use http(s)")
    if parts.username or parts.password:
        raise UnsafeDestinationError("Destination URL must not embed credentials")
    if not parts.hostname:
        raise UnsafeDestinationError("Destination URL has no host")
    effective_port = port or (443 if parts.scheme.lower() == "https" else 80)
    return await resolve_public_ips(parts.hostname, effective_port, resolver=resolver)
