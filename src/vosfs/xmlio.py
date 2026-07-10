"""Shared safe XML parsing for VOSpace response documents.

Every VOSpace document (nodes, listings, capabilities, transfer details) is
parsed through :func:`safe_parse`, which bounds the body before parsing and
uses :mod:`defusedxml` to reject DTDs and external entities.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from xml.etree.ElementTree import ParseError

from defusedxml.ElementTree import fromstring as _safe_fromstring

if TYPE_CHECKING:
    import xml.etree.ElementTree as ET

#: Default 8 MiB ceiling applied to a response body before it is parsed.
DEFAULT_LIMIT = 8_388_608


def safe_parse(data: bytes, *, limit: int = DEFAULT_LIMIT) -> ET.Element:
    """Bound and defuse ``data``, returning the parsed document root.

    Args:
        data: The raw XML body.
        limit: Maximum accepted size in bytes, enforced before parsing.

    Returns:
        The parsed root element.

    Raises:
        ValueError: If the body exceeds ``limit`` or is not well-formed XML.
            DTDs and external entities are rejected by :mod:`defusedxml`, which
            also raises :class:`ValueError`.
    """
    if len(data) > limit:
        msg = f"XML body exceeds {limit} byte limit"
        raise ValueError(msg)
    try:
        return _safe_fromstring(data, forbid_dtd=True)
    except ParseError as exc:
        msg = "malformed XML document"
        raise ValueError(msg) from exc
