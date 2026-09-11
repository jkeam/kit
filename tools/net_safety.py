"""SSRF guard for outbound HTTP fetches driven by LLM tool calls
(web_fetch, knowledge ingest_url).

A prompt-injected or malicious URL could otherwise be used to reach cloud
metadata endpoints (169.254.169.254), internal services, or loopback. This
resolves the hostname (and every redirect hop) and rejects private/
loopback/link-local/reserved targets before the request is made, rather
than only checking the literal hostname string.
"""

import ipaddress
import socket
from urllib.parse import urlparse

import httpx

MAX_REDIRECTS = 5
ALLOWED_SCHEMES = {"http", "https"}


class UnsafeURLError(ValueError):
    """Raised when a URL is blocked (bad scheme, or resolves to a
    private/internal/loopback address)."""


def _is_blocked_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # unparseable address -> fail closed
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def assert_url_is_safe(url: str) -> None:
    """Raise UnsafeURLError if `url` has a disallowed scheme or its
    hostname resolves to a private/internal/loopback/link-local address."""
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise UnsafeURLError(f"Blocked URL scheme: {parsed.scheme!r}")

    hostname = parsed.hostname
    if not hostname:
        raise UnsafeURLError("URL has no hostname")

    if hostname.lower() == "localhost":
        raise UnsafeURLError("Blocked host: localhost")

    try:
        addrinfo = socket.getaddrinfo(hostname, None)
    except socket.gaierror as e:
        raise UnsafeURLError(f"Could not resolve host {hostname!r}: {e}")

    for family, _, _, _, sockaddr in addrinfo:
        ip_str = sockaddr[0]
        if _is_blocked_ip(ip_str):
            raise UnsafeURLError(
                f"Blocked target address {ip_str} (resolved from {hostname!r})"
            )


def safe_get(url: str, *, timeout: float, headers: dict = None) -> httpx.Response:
    """httpx.get() with SSRF protection.

    Validates the URL (and every redirect hop, since a safe initial host
    could still redirect to an internal one) before requesting it. Manual
    redirect following is required for this — httpx's follow_redirects=True
    doesn't give us a chance to inspect each hop before it's fetched.
    """
    current_url = url
    for _ in range(MAX_REDIRECTS + 1):
        assert_url_is_safe(current_url)
        response = httpx.get(current_url, follow_redirects=False, timeout=timeout, headers=headers)
        if response.is_redirect:
            location = response.headers.get("location")
            if not location:
                return response
            current_url = str(httpx.URL(current_url).join(location))
            continue
        return response

    raise UnsafeURLError(f"Too many redirects (> {MAX_REDIRECTS})")
