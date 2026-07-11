"""VOSpace filesystem path identity.

This module implements the path normalization and validation rules from the
v0.3.0 contract. The service is selected only by ``endpoint_url``; the apparent
authority after the ``vos://`` protocol marker is part of the filesystem path,
not a service or VOSpace authority.
"""

from __future__ import annotations

from urllib.parse import quote, unquote

PROTOCOL = "vos"

_SCHEME_PREFIX = f"{PROTOCOL}:"
# Percent-encodings that would smuggle a path separator into a single segment.
_ENCODED_SEPARATORS = ("%2f", "%5c")


def strip_protocol(path: str) -> str:
    """Return the canonical internal path for ``path``.

    The inputs ``vos://a/b``, ``vos:///a/b``, ``/a/b``, and ``a/b`` all
    normalize to ``/a/b``. ``vos://``, ``vos:///``, ``/``, and the empty path
    represent root and normalize to ``/``.

    Args:
        path: A user-supplied VOSpace path in any accepted form.

    Returns:
        The normalized path: a leading slash followed by percent-decoded
        segments, or ``/`` for root.

    Raises:
        ValueError: If the path contains a query, fragment, userinfo, NUL byte,
            an encoded path separator, or a ``..`` segment.
    """
    if "\x00" in path:
        msg = "path must not contain a NUL byte"
        raise ValueError(msg)
    if "?" in path:
        msg = "path must not contain a query component"
        raise ValueError(msg)
    if "#" in path:
        msg = "path must not contain a fragment component"
        raise ValueError(msg)

    remainder = _strip_scheme(path)
    remainder = _absorb_authority(remainder)
    # Collapse any run of leading slashes into a single root slash.
    remainder = "/" + remainder.lstrip("/")

    decoded = [_decode_segment(segment) for segment in remainder.split("/") if segment]
    if not decoded:
        return "/"
    return "/" + "/".join(decoded)


def _strip_scheme(path: str) -> str:
    """Remove a leading ``vos:`` scheme marker, case-insensitively."""
    if path[: len(_SCHEME_PREFIX)].lower() == _SCHEME_PREFIX:
        return path[len(_SCHEME_PREFIX) :]
    return path


def _absorb_authority(remainder: str) -> str:
    """Fold a ``//authority`` marker into the path, rejecting userinfo.

    The authority position after ``//`` is treated as leading path content, so
    ``//a/b`` becomes ``/a/b``. A ``user@host`` style authority carries
    userinfo, which is rejected.
    """
    if not remainder.startswith("//"):
        return remainder
    authority = remainder[2:].split("/", 1)[0]
    if "@" in authority:
        msg = "path must not contain userinfo"
        raise ValueError(msg)
    return "/" + remainder[2:]


def _decode_segment(segment: str) -> str:
    """Percent-decode one path segment exactly once and validate it."""
    lowered = segment.lower()
    for encoded in _ENCODED_SEPARATORS:
        if encoded in lowered:
            msg = "path must not contain an encoded path separator"
            raise ValueError(msg)
    decoded = unquote(segment, encoding="utf-8", errors="strict")
    if decoded in ("..", "."):
        msg = "path must not contain a relative segment"
        raise ValueError(msg)
    if "\x00" in decoded:
        msg = "path must not contain a NUL byte"
        raise ValueError(msg)
    return decoded


def parent(path: str) -> str:
    """Return the parent of an already-normalized path, or ``/`` for root.

    The input is assumed already normalized (percent-decoded exactly once by
    :func:`strip_protocol`); it is not decoded again.
    """
    if path == "/":
        return "/"
    head = path.rsplit("/", 1)[0]
    return head or "/"


def segments(path: str) -> list[str]:
    """Return the segments of an already-normalized path (no further decoding)."""
    return [segment for segment in path.split("/") if segment]


def encode_url_path(path: str) -> str:
    """Return the normalized path with each segment percent-encoded for a URL.

    Operates on the already-decoded internal path and re-encodes exactly once,
    so normalization stays idempotent (root becomes an empty string so it can be
    appended to a base URL).
    """
    return "".join(f"/{quote(segment, safe='')}" for segment in segments(path))
