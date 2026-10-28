"""URL safety check for open_url / web_search (Stage 6a).

Only plain http/https addresses to public websites are allowed. Anything else
(javascript:, file:, data:, localhost, private IPs, user:pass@host, look-alike
punycode names) is refused before a browser is ever started.
"""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

MAX_URL_CHARS = 2000
_BAD_CHARS = re.compile(r"[\s\x00-\x1f\x7f<>\"'`\\]")
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)
_HOST_PORT = re.compile(r"^[\w.-]+:\d+")
_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_LOCAL_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home", ".corp", ".intranet")


class UnsafeUrl(Exception):
    """The address is not allowed. Message is for logs, not for speech."""


def normalize_url(raw: str, allow_punycode: bool = False) -> str:
    s = (raw or "").strip()
    if not s:
        raise UnsafeUrl("empty address")
    if len(s) > MAX_URL_CHARS:
        raise UnsafeUrl("address too long")
    if _BAD_CHARS.search(s):
        raise UnsafeUrl("address has spaces or unsafe characters")

    if "://" not in s:
        if _SCHEME.match(s) and not _HOST_PORT.match(s):
            raise UnsafeUrl("only http and https are allowed")
        s = "https://" + s

    p = urlparse(s)
    if p.scheme.lower() not in ("http", "https"):
        raise UnsafeUrl("only http and https are allowed")
    if p.username or p.password or "@" in p.netloc:
        raise UnsafeUrl("addresses with a user name or password are not allowed")
    try:
        _ = p.port
    except ValueError:
        raise UnsafeUrl("bad port")

    host = (p.hostname or "").lower().rstrip(".")
    if not host:
        raise UnsafeUrl("no website name")

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if not ip.is_global:
            raise UnsafeUrl("private or local network address")
    else:
        if host == "localhost" or host.endswith(_LOCAL_SUFFIXES):
            raise UnsafeUrl("local address")
        labels = host.split(".")
        if len(labels) < 2 or len(host) > 253:
            raise UnsafeUrl("not a full website name")
        if not all(_LABEL.match(l) for l in labels):
            raise UnsafeUrl("bad website name")
        if not allow_punycode and any(l.startswith("xn--") for l in labels):
            raise UnsafeUrl("look-alike (punycode) website name")
        tld = labels[-1]
        if not (tld.isalpha() or tld.startswith("xn--")) or len(tld) < 2:
            raise UnsafeUrl("bad ending in website name")
    netloc = host if ":" not in host else f"[{host}]"
    if p.port:
        netloc += f":{p.port}"
    return p._replace(scheme=p.scheme.lower(), netloc=netloc).geturl()
