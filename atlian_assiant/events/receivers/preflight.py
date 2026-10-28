"""WebhookUrlPreflight (design 14.5): check EV section 3.1 receiver requirements BEFORE a callback is registered."""
from __future__ import annotations

import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

import anyio
import httpx

DEFAULT_ALLOWED_PORTS = frozenset({443})        # the documented set is VER-17: pass `allowed_ports` to widen it once confirmed


@dataclass(frozen=True)
class PreflightReport:
    url: str
    ok: bool
    problems: tuple[str, ...]


@dataclass(frozen=True)
class ProbeResult:
    tls_ok: bool
    tls_detail: str = ""
    status: int | None = None
    location: str | None = None


Resolver = Callable[[str, int], Awaitable[list[str]]]
Prober = Callable[[str], Awaitable[ProbeResult]]


async def _resolve(host: str, port: int) -> list[str]:
    infos = await anyio.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


async def _probe(url: str) -> ProbeResult:
    """TLS-verifying request that never follows redirects (an unauthenticated POST with an empty JSON body)."""
    try:
        async with httpx.AsyncClient(verify=True, follow_redirects=False, timeout=10.0) as http:
            resp = await http.post(url, content=b"{}", headers={"Content-Type": "application/json", "X-Atlassian-Preflight": "1"})
    except httpx.ConnectError as exc:
        return ProbeResult(tls_ok="CERTIFICATE" not in str(exc).upper(), tls_detail=str(exc))
    except httpx.HTTPError as exc:
        return ProbeResult(tls_ok=True, tls_detail=f"{type(exc).__name__}: {exc}")
    return ProbeResult(tls_ok=True, status=resp.status_code, location=resp.headers.get("location"))


def is_public(address: str) -> bool:
    """False for RFC 1918, loopback, link-local, CGNAT (100.64/10), ULA, multicast, reserved and unspecified addresses."""
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


class WebhookUrlPreflight:
    """Checks EV section 3.1 receiver requirements BEFORE any callback is registered (DM-01..04, 09, 13, 15, 16, 44).

    1. scheme is https
    2. port is in the documented allowed set (configurable list, kept current with Atlassian docs)
    3. every resolved address is public: rejects RFC 1918, loopback, link-local, CGNAT, ULA
    4. TLS chain validates against a public CA bundle and the hostname matches
    5. a probe request is not answered with a redirect
    """

    def __init__(self, allowed_ports: frozenset[int] | None = None, *, resolver: Resolver = _resolve, prober: Prober = _probe,
                 network: bool = True) -> None:
        self.allowed_ports = allowed_ports
        self._resolve, self._probe, self._network = resolver, prober, network

    async def check(self, url: str) -> PreflightReport:
        problems: list[str] = []
        parts = urlsplit(url)
        if parts.scheme != "https":
            problems.append(f"scheme must be https (got {parts.scheme or 'none'!r})")
        host = parts.hostname
        if not host:
            return PreflightReport(url, False, (*problems, "URL has no host"))
        try:
            port = parts.port or 443
        except ValueError:
            return PreflightReport(url, False, (*problems, "URL has an invalid port"))
        allowed = self.allowed_ports if self.allowed_ports is not None else DEFAULT_ALLOWED_PORTS
        if port not in allowed:
            problems.append(f"port {port} is not in the allowed callback ports {sorted(allowed)}")
        if parts.username or parts.password:
            problems.append("credentials in the URL are not allowed")
        addresses: list[str]
        try:
            ipaddress.ip_address(host)
            addresses = [host]
        except ValueError:
            try:
                addresses = await self._resolve(host, port)
            except OSError as exc:
                return PreflightReport(url, False, (*problems, f"host {host!r} does not resolve ({exc})"))
        private = [a for a in addresses if not is_public(a)]
        if private or not addresses:
            problems.append("address is not publicly routable (" + (", ".join(private) or "no addresses") + "): Atlassian Cloud cannot reach "
                            "RFC 1918, loopback, link-local, CGNAT or ULA targets; use a managed relay, tunnel or a pull method")
        if self._network and not problems:
            result = await self._probe(url)
            if not result.tls_ok:
                problems.append(f"TLS validation failed: {result.tls_detail}")
            if result.status is not None and 300 <= result.status < 400:
                problems.append(f"probe was answered with a redirect ({result.status} -> {result.location}); redirects are limited by Atlassian")
        return PreflightReport(url, not problems, tuple(problems))
