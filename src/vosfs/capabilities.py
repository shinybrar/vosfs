"""VOSI capability discovery and the immutable service-binding model.

The filesystem fetches ``endpoint_url + "/capabilities"`` on first I/O and
resolves only the node and synchronous-transfer bindings, selected by exact
capability identifier and standard-role ParamHTTP interface, validating the
configured credential against each binding's advertised security methods. No
operation URL is ever guessed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from vosfs.xmlio import local_name, safe_parse

if TYPE_CHECKING:
    import xml.etree.ElementTree as ET

# Exact capability identifiers fixed by the contract (section 5).
NODES_STANDARD_ID = "ivo://ivoa.net/std/VOSpace/v2.0#nodes"
SYNC_STANDARD_ID = "ivo://ivoa.net/std/VOSpace#sync-2.1"

# Supported security-method identifiers. The empty string is the anonymous
# method (an interface with no ``securityMethod`` children). These are IVOA
# method identifiers, not secrets.
ANONYMOUS_METHOD = ""
TOKEN_METHOD = "ivo://ivoa.net/sso#token"  # noqa: S105
CERTIFICATE_METHOD = "ivo://ivoa.net/sso#tls-with-certificate"

_XSI_TYPE_ATTR = "{http://www.w3.org/2001/XMLSchema-instance}type"
_PARAM_HTTP = "ParamHTTP"
_STANDARD_ROLE = "std"


@dataclass(frozen=True)
class ServiceBindings:
    """The resolved node and synchronous-transfer operation URLs.

    A ``None`` URL means the deployment did not advertise that binding; the
    dependent operation raises ``NotImplementedError`` when used.
    """

    nodes_url: str | None
    sync_url: str | None

    def require_nodes(self) -> str:
        """Return the node binding URL or raise if it was not advertised."""
        return _require(self.nodes_url, "node")

    def require_sync(self) -> str:
        """Return the synchronous-transfer binding URL or raise if absent."""
        return _require(self.sync_url, "synchronous-transfer")


def _require(url: str | None, name: str) -> str:
    if url is None:
        msg = f"the OpenCADC service does not advertise a {name} binding"
        raise NotImplementedError(msg)
    return url


def parse_bindings(data: bytes, *, security_method: str) -> ServiceBindings:
    """Resolve the node and sync bindings for the configured credential.

    Args:
        data: The raw ``/capabilities`` response body.
        security_method: The configured credential's security-method
            identifier (``ANONYMOUS_METHOD``, ``TOKEN_METHOD``, or
            ``CERTIFICATE_METHOD``).

    Returns:
        The resolved bindings, with a ``None`` URL for any binding the
        deployment does not advertise.

    Raises:
        ValueError: If the capabilities document is malformed.
        PermissionError: If an advertised binding does not accept the
            configured credential's security method.
    """
    root = safe_parse(data)
    return ServiceBindings(
        nodes_url=_resolve(root, NODES_STANDARD_ID, security_method, use="base"),
        sync_url=_resolve(root, SYNC_STANDARD_ID, security_method, use="full"),
    )


def _resolve(
    root: ET.Element, standard_id: str, security_method: str, *, use: str
) -> str | None:
    """Resolve one capability's access URL for the configured credential."""
    capability = _find_capability(root, standard_id)
    if capability is None:
        return None
    matched = [
        interface
        for interface in _standard_param_http_interfaces(capability)
        if _accepts(interface, security_method)
    ]
    if not matched:
        msg = (
            f"the {standard_id} binding does not advertise a supported security "
            f"method for the configured credential"
        )
        raise PermissionError(msg)
    return _access_url(matched[0], use=use)


def _find_capability(root: ET.Element, standard_id: str) -> ET.Element | None:
    for element in root.iter():
        if (
            local_name(element.tag) == "capability"
            and element.get("standardID") == standard_id
        ):
            return element
    return None


def _standard_param_http_interfaces(capability: ET.Element) -> list[ET.Element]:
    interfaces = []
    for element in capability:
        if (
            local_name(element.tag) != "interface"
            or element.get("role") != _STANDARD_ROLE
        ):
            continue
        xsi_type = element.get(_XSI_TYPE_ATTR, "")
        if xsi_type.rsplit(":", 1)[-1] == _PARAM_HTTP:
            interfaces.append(element)
    return interfaces


def _accepts(interface: ET.Element, security_method: str) -> bool:
    """Whether an interface advertises the configured credential's method."""
    advertised = {
        element.get("standardID", "")
        for element in interface
        if local_name(element.tag) == "securityMethod"
    }
    advertised.discard("")
    if security_method == ANONYMOUS_METHOD:
        return not advertised
    return security_method in advertised


def _access_url(interface: ET.Element, *, use: str) -> str | None:
    """Return the access URL matching ``use``, or an unqualified one.

    An ``accessURL`` whose ``use`` differs (for example a ``full`` URL when a
    ``base`` URL is required, or the reverse) is never returned: the two are not
    interchangeable — a ``base`` URL is meant to have a node path appended, a
    ``full`` URL is used verbatim. Only an ``accessURL`` that omits ``use``
    entirely is accepted as a fallback.
    """
    urls = [element for element in interface if local_name(element.tag) == "accessURL"]
    for element in urls:
        if element.get("use") == use and element.text:
            return element.text.strip()
    for element in urls:
        if element.get("use") is None and element.text:
            return element.text.strip()
    return None
