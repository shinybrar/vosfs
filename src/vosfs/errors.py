"""VOSpace error taxonomy, redaction, and HTTP/transport mapping.

This module implements the failure contract from v0.3.0 (contract section 13).
Well-understood failures map onto standard exceptions (``ValueError``,
``PermissionError``, ``FileNotFoundError``, ``FileExistsError``,
``NotImplementedError``, ``OSError`` with ``errno.ENOSPC``, ``BlockingIOError``,
``TimeoutError``, and ``ConnectionError``). Every remaining OpenCADC,
integrity, HTTP, and partial-completion failure is represented by the single
public :class:`VOSpaceError`, which retains the HTTP status, symbolic fault,
retry guidance, and the completed and failed paths of a partial operation.

Error bodies are bounded to :data:`MAX_ERROR_BODY` bytes before parsing or
reporting, and credentials, cookie values, and pre-authorized URL tokens are
redacted from every message, representation, log line, and recorded fixture by
:func:`redact`.
"""

from __future__ import annotations

import errno
import re

import httpx

MAX_ERROR_BODY: int = 8192
"""Maximum number of body bytes decoded before parsing or reporting (8 KiB)."""

_REDACTED = "<redacted>"

# HTTP status codes with a dedicated standard-exception mapping.
_STATUS_UNAUTHORIZED = 401
_STATUS_FORBIDDEN = 403
_STATUS_NOT_FOUND = 404
_STATUS_CONFLICT = 409
_STATUS_PAYLOAD_TOO_LARGE = 413
_STATUS_LOCKED = 423

# Statuses that map one-to-one onto a standard ``OSError`` subclass.
_STATUS_EXCEPTIONS: dict[int, type[OSError]] = {
    _STATUS_UNAUTHORIZED: PermissionError,
    _STATUS_FORBIDDEN: PermissionError,
    _STATUS_NOT_FOUND: FileNotFoundError,
    _STATUS_CONFLICT: FileExistsError,
    _STATUS_LOCKED: BlockingIOError,
}

# Secret patterns, applied in order. Each replaces the secret value with
# ``_REDACTED`` while preserving the surrounding, non-secret context.
_COOKIE_RE = re.compile(r"(?i)((?:set-)?cookie:[ \t]*)[^\r\n]*")
_AUTHORIZATION_RE = re.compile(r"(?i)(authorization:[ \t]*)[^\r\n]*")
_BEARER_RE = re.compile(r"(?i)(bearer[ \t]+)[A-Za-z0-9._~+/=-]+")
_URL_SECRET_RE = re.compile(
    r"(?i)([?&](?:token|signature|sig|access_token"
    r"|x-amz-signature|x-amz-credential|x-amz-security-token"
    r"|awsaccesskeyid)=)[^&\s#]*",
)
_OPAQUE_SEGMENT_RE = re.compile(
    r"(?i)(/(?:preauth|authorization|credential|token|signature|auth|sig|cred)/)"
    r"[A-Za-z0-9._~=+-]{20,}",
)

_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (_COOKIE_RE, rf"\1{_REDACTED}"),
    (_AUTHORIZATION_RE, rf"\1{_REDACTED}"),
    (_BEARER_RE, rf"\1{_REDACTED}"),
    (_URL_SECRET_RE, rf"\1{_REDACTED}"),
    (_OPAQUE_SEGMENT_RE, rf"\1{_REDACTED}"),
)


class VOSpaceError(OSError):
    """A generic OpenCADC, integrity, HTTP, or partial-completion failure.

    This is the single public exception for failures without a dedicated
    standard mapping. It retains diagnostic context while guaranteeing that its
    string form and representation are redacted of any secret material.

    Attributes:
        status: The HTTP status that triggered the failure, if any.
        fault: The symbolic OpenCADC fault name, if reported.
        retry_after: Server retry guidance in seconds, if provided.
        completed: Paths that completed before a partial failure.
        failed: Paths that failed during a partial operation.
    """

    def __init__(  # noqa: PLR0913 - the field set is mandated by the error contract
        self,
        message: str,
        *,
        status: int | None = None,
        fault: str | None = None,
        retry_after: float | None = None,
        completed: list[str] | None = None,
        failed: list[str] | None = None,
    ) -> None:
        """Initialize the error and redact its message.

        Args:
            message: The human-readable description of the failure.
            status: The HTTP status that triggered the failure, if any.
            fault: The symbolic OpenCADC fault name, if reported.
            retry_after: Server retry guidance in seconds, if provided.
            completed: Paths that completed before a partial failure.
            failed: Paths that failed during a partial operation.
        """
        self.status: int | None = status
        self.fault: str | None = fault
        self.retry_after: float | None = retry_after
        self.completed: list[str] = completed if completed is not None else []
        self.failed: list[str] = failed if failed is not None else []
        super().__init__(redact(_describe(message, status, fault)))

    def __repr__(self) -> str:
        """Return a redacted representation carrying the diagnostic context."""
        return (
            f"{type(self).__name__}({self.args[0]!r}, status={self.status!r}, "
            f"fault={self.fault!r}, retry_after={self.retry_after!r})"
        )


def _describe(message: str, status: int | None, fault: str | None) -> str:
    """Return the message annotated with the status and fault when present.

    Args:
        message: The base failure description.
        status: The HTTP status to append, if any.
        fault: The symbolic fault name to append, if any.

    Returns:
        The message with ``(HTTP <status>)`` and ``[<fault>]`` appended when
        those values are available.
    """
    parts = [message]
    if status is not None:
        parts.append(f"(HTTP {status})")
    if fault is not None:
        parts.append(f"[{fault}]")
    return " ".join(parts)


def _is_quota_fault(fault: str | None) -> bool:
    """Return whether a symbolic fault name denotes quota exhaustion.

    Args:
        fault: The symbolic OpenCADC fault name, if any.

    Returns:
        ``True`` when the fault names a quota condition, otherwise ``False``.
    """
    return fault is not None and "quota" in fault.lower()


def bounded_text(data: bytes, limit: int = MAX_ERROR_BODY) -> str:
    """Decode at most ``limit`` bytes of ``data`` as UTF-8.

    Args:
        data: The raw response body.
        limit: The maximum number of bytes to decode. Defaults to
            :data:`MAX_ERROR_BODY`.

    Returns:
        The decoded, whitespace-stripped text of the first ``limit`` bytes,
        with undecodable bytes replaced.
    """
    return data[:limit].decode("utf-8", errors="replace").strip()


def redact(text: str) -> str:
    """Replace credentials, cookies, and URL tokens in ``text``.

    The following secrets are replaced with ``<redacted>``: ``Authorization``
    header values and bare ``Bearer`` tokens, ``Cookie`` and ``Set-Cookie``
    header values, pre-authorized query parameters (``token``, ``signature``,
    ``sig``, ``access_token``, ``X-Amz-Signature``, ``X-Amz-Credential``), and
    long opaque URL path segments that follow a pre-authorization marker. The
    match is conservative: text without a secret pattern is returned unchanged.

    Args:
        text: The text that may contain secret material.

    Returns:
        The text with every recognized secret value replaced.
    """
    result = text
    for pattern, replacement in _REDACTIONS:
        result = pattern.sub(replacement, result)
    return result


def http_exception(
    status: int,
    *,
    body: str = "",
    fault: str | None = None,
    path: str | None = None,
    retry_after: float | None = None,
) -> Exception:
    """Map an HTTP status onto the exception required by the contract.

    The returned exception is not raised. Authentication and authorization
    (401, 403) map to :class:`PermissionError`; a missing node (404) to
    :class:`FileNotFoundError`; a conflict (409) to :class:`FileExistsError`; a
    locked node (423) to :class:`BlockingIOError`; quota exhaustion (413 or a
    quota fault) to ``OSError`` with :data:`errno.ENOSPC`. Every other status,
    including 5xx, 412, and an ambiguous 400, becomes a :class:`VOSpaceError`
    carrying the status, fault, and retry guidance.

    Args:
        status: The HTTP status code of the failed response.
        body: The already-bounded response body text; a redacted snippet is
            incorporated into the message.
        fault: The symbolic OpenCADC fault name, if reported.
        path: The VOSpace path involved, included in the message when given.
        retry_after: Server retry guidance in seconds, if provided.

    Returns:
        The mapped exception instance.
    """
    snippet = redact(body)
    location = f" for {path}" if path else ""
    detail = f": {snippet}" if snippet else ""
    base = f"VOSpace request failed{location}{detail}"

    if status == _STATUS_PAYLOAD_TOO_LARGE or _is_quota_fault(fault):
        return OSError(errno.ENOSPC, f"{base} (HTTP {status})")

    factory = _STATUS_EXCEPTIONS.get(status)
    if factory is not None:
        return factory(f"{base} (HTTP {status})")

    return VOSpaceError(base, status=status, fault=fault, retry_after=retry_after)


def transport_exception(exc: Exception, *, path: str | None = None) -> Exception:
    """Map an HTTPX transport error onto the exception required by the contract.

    The returned exception is not raised. Connection failures
    (:class:`httpx.ConnectError`, :class:`httpx.ConnectTimeout`, and any other
    :class:`httpx.TransportError`) map to :class:`ConnectionError`; a remaining
    :class:`httpx.TimeoutException` maps to :class:`TimeoutError`; anything else
    becomes a :class:`VOSpaceError` with a redacted detail. ``ConnectTimeout``
    is checked before the timeout branch because it subclasses
    :class:`httpx.TimeoutException` yet denotes a connection failure.

    Args:
        exc: The transport-layer exception raised by HTTPX.
        path: The VOSpace path involved, included in the message when given.

    Returns:
        The mapped exception instance.
    """
    location = f" for {path}" if path else ""
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return ConnectionError(f"VOSpace connection failed{location}")
    if isinstance(exc, httpx.TimeoutException):
        return TimeoutError(f"VOSpace request timed out{location}")
    if isinstance(exc, httpx.TransportError):
        return ConnectionError(f"VOSpace connection failed{location}")
    return VOSpaceError(f"VOSpace transport error{location}: {redact(str(exc))}")
