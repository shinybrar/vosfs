"""Opt-in, project-owned HTTPX transport recorder for sanitized fixtures.

Recording is disabled by default and in normal CI (contract section 15.2). When
enabled, the recorder wraps a real HTTPX transport, captures request/response
pairs, and sanitizes them before they may be persisted: Authorization,
Proxy-Authorization, Cookie, and Set-Cookie values; certificate and
username/password material; pre-authorized tokens embedded in URLs; and other
volatile or personal values. Sanitized fixtures are regression evidence, not
release evidence, and must be manually reviewed before they are committed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

if TYPE_CHECKING:
    from collections.abc import Mapping

_REDACTED = "<redacted>"
_SECRET_HEADERS = frozenset(
    {"authorization", "proxy-authorization", "cookie", "set-cookie", "content-md5"},
)
_SECRET_QUERY_KEYS = re.compile(
    r"(?i)(token|signature|sig|access_token|x-amz-signature|x-amz-credential)",
)
# OpenCADC pre-authorized URLs carry the token as a ``preauth:<token>`` path
# segment (e.g. ``/files/preauth:<token>/cadc:PATH/file``), never as a query
# parameter, so the path itself must be sanitized.
_SECRET_PATH_TOKEN = re.compile(r"(?i)(preauth:)[^/]+")


@dataclass
class Interaction:
    """One sanitized request/response pair captured by the recorder."""

    method: str
    url: str
    request_headers: dict[str, str]
    status: int
    response_headers: dict[str, str]
    body_sha256: str


@dataclass
class Recorder:
    """Accumulates sanitized interactions for optional fixture persistence."""

    interactions: list[Interaction] = field(default_factory=list)

    def record(  # noqa: PLR0913 - one keyword arg per interaction field
        self,
        *,
        method: str,
        url: str,
        request_headers: Mapping[str, str],
        status: int,
        response_headers: Mapping[str, str],
        body_sha256: str,
    ) -> None:
        """Append one interaction after sanitizing its headers and URL."""
        self.interactions.append(
            Interaction(
                method=method,
                url=sanitize_url(url),
                request_headers=sanitize_headers(request_headers),
                status=status,
                response_headers=sanitize_headers(response_headers),
                body_sha256=body_sha256,
            ),
        )


def sanitize_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Return headers with secret values replaced by a redaction marker."""
    return {
        key: (_REDACTED if key.lower() in _SECRET_HEADERS else value)
        for key, value in headers.items()
    }


def sanitize_url(url: str) -> str:
    """Redact userinfo, pre-authorized path tokens, and secret query params."""
    parts = urlsplit(url)
    netloc = parts.hostname or ""
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    path = _SECRET_PATH_TOKEN.sub(rf"\1{_REDACTED}", parts.path)
    query = urlencode(
        [
            (key, _REDACTED if _SECRET_QUERY_KEYS.search(key) else value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
        ],
    )
    return urlunsplit((parts.scheme, netloc, path, query, ""))
