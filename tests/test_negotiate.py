"""Tests for synchronous byte negotiation and credential routing (section 7)."""

import httpx
import pytest
import respx
from conftest import (
    AUTHORITY,
    BASE_URL,
    NODES_URL,
    SYNC_URL,
    make_fs,
    mock_capabilities,
)

from vosfs import negotiate
from vosfs.capabilities import ANONYMOUS_METHOD, CERTIFICATE_METHOD, TOKEN_METHOD
from vosfs.negotiate import (
    DIRECTION_PULL,
    PROTOCOL_HTTPS_GET,
    NegotiatedEndpoint,
    Protocol,
    build_target_uri,
    choose_protocol,
    parse_transfer_details,
    validate_redirect,
)

VOS = 'xmlns:vos="http://www.ivoa.net/xml/VOSpace/v2.0"'
XSI = 'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
ROOT = (
    f'<vos:node {VOS} {XSI} xsi:type="vos:ContainerNode" uri="vos://{AUTHORITY}">'
    "<vos:properties/><vos:nodes/></vos:node>"
).encode()


def _details(endpoint: str, *, security_method: str | None = None) -> bytes:
    security = (
        f'<vos:securityMethod standardID="{security_method}"/>'
        if security_method
        else ""
    )
    return f"""<vos:transfer {VOS} version="2.1">
      <vos:target>vos://{AUTHORITY}/file.txt</vos:target>
      <vos:direction>pullFromVoSpace</vos:direction>
      <vos:protocol uri="ivo://ivoa.net/vospace/core#httpsget">
        <vos:endpoint>{endpoint}</vos:endpoint>
        {security}
      </vos:protocol>
    </vos:transfer>""".encode()


# --- pure helpers ---------------------------------------------------------------


def test_build_target_uri() -> None:
    assert build_target_uri("x!vault", "/a/b") == "vos://x!vault/a/b"


def test_parse_transfer_details_anonymous() -> None:
    protocols = parse_transfer_details(_details("https://h.test/files/x"))
    assert protocols == [Protocol("https://h.test/files/x", ANONYMOUS_METHOD)]


def test_parse_transfer_details_with_security_method() -> None:
    protocols = parse_transfer_details(
        _details("https://h.test/f", security_method=TOKEN_METHOD)
    )
    assert protocols[0].security_method == TOKEN_METHOD


def test_parse_transfer_details_without_endpoint_raises() -> None:
    empty = f'<vos:transfer {VOS} version="2.1"></vos:transfer>'.encode()
    with pytest.raises(OSError, match="no usable protocol"):
        parse_transfer_details(empty)


def test_choose_protocol_prefers_compatible() -> None:
    protocols = [
        Protocol("https://h/cert", CERTIFICATE_METHOD),
        Protocol("https://h/anon", ANONYMOUS_METHOD),
    ]
    assert choose_protocol(protocols, TOKEN_METHOD).endpoint == "https://h/anon"


def test_choose_protocol_no_match_raises() -> None:
    protocols = [Protocol("https://h/cert", CERTIFICATE_METHOD)]
    with pytest.raises(OSError, match="no negotiated endpoint"):
        choose_protocol(protocols, TOKEN_METHOD)


@pytest.mark.parametrize(
    ("location", "sending_bearer"),
    [
        (None, False),
        ("ftp://h/x", False),
        ("https://u:p@h/x", False),
        ("http://h/x", True),
    ],
)
def test_validate_redirect_rejections(
    location: str | None, sending_bearer: bool
) -> None:
    with pytest.raises(OSError, match=r"redirect|Location|bearer"):
        validate_redirect(location, base=SYNC_URL, sending_bearer=sending_bearer)


def test_validate_redirect_resolves_relative() -> None:
    # An absolute-path Location resolves against the host root, per URL rules.
    resolved = validate_redirect(
        "/synctrans/results", base=SYNC_URL, sending_bearer=False
    )
    assert resolved == "https://staging.canfar.net/synctrans/results"


def test_parse_transfer_details_skips_protocol_without_endpoint() -> None:
    doc = f"""<vos:transfer {VOS} version="2.1">
      <vos:protocol uri="ivo://ivoa.net/vospace/core#httpsget"/>
    </vos:transfer>""".encode()
    with pytest.raises(OSError, match="no usable protocol"):
        parse_transfer_details(doc)


# --- negotiation through the filesystem -----------------------------------------


def _mock_negotiation(
    router: respx.Router, endpoint: str, *, security_method: str | None = None
) -> None:
    mock_capabilities(router)
    router.get(NODES_URL).mock(return_value=httpx.Response(200, content=ROOT))
    details_url = f"{BASE_URL}/synctrans/results"
    router.post(SYNC_URL).mock(
        return_value=httpx.Response(303, headers={"Location": details_url}),
    )
    router.get(details_url).mock(
        return_value=httpx.Response(
            200, content=_details(endpoint, security_method=security_method)
        ),
    )


async def test_negotiate_read_returns_endpoint(router: respx.Router) -> None:
    endpoint = f"{BASE_URL}/files/abc"
    _mock_negotiation(router, endpoint)
    fs = make_fs(router, asynchronous=True)
    negotiated = await fs._negotiate(
        "/file.txt", direction=DIRECTION_PULL, protocol_uri=PROTOCOL_HTTPS_GET
    )
    assert negotiated == NegotiatedEndpoint(endpoint, ANONYMOUS_METHOD)
    await fs.aclose()


async def test_negotiate_non_303_maps_error(router: respx.Router) -> None:
    mock_capabilities(router)
    router.get(NODES_URL).mock(return_value=httpx.Response(200, content=ROOT))
    router.post(SYNC_URL).mock(return_value=httpx.Response(400, text="bad transfer"))
    fs = make_fs(router, asynchronous=True)
    with pytest.raises(OSError, match="400"):
        await fs._negotiate(
            "/file.txt", direction=DIRECTION_PULL, protocol_uri=PROTOCOL_HTTPS_GET
        )
    await fs.aclose()


async def test_byte_send_anonymous_has_no_auth(router: respx.Router) -> None:
    seen: dict[str, str | None] = {}

    def capture(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, content=b"hello")

    router.get(f"{BASE_URL}/files/abc").side_effect = capture
    fs = make_fs(router, asynchronous=True, token="secret")
    endpoint = NegotiatedEndpoint(f"{BASE_URL}/files/abc", ANONYMOUS_METHOD)
    response = await fs._byte_send(endpoint, "GET")
    assert response.content == b"hello"
    assert seen["auth"] is None
    await fs.aclose()


async def test_byte_send_token_endpoint_sends_bearer(router: respx.Router) -> None:
    seen: dict[str, str | None] = {}

    def capture(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200)

    router.get("https://cross.test/files/x").side_effect = capture
    fs = make_fs(router, asynchronous=True, token="tok")
    endpoint = NegotiatedEndpoint("https://cross.test/files/x", TOKEN_METHOD)
    await fs._byte_send(endpoint, "GET")
    assert seen["auth"] == "Bearer tok"
    await fs.aclose()


async def test_byte_send_token_endpoint_requires_https(router: respx.Router) -> None:
    fs = make_fs(router, asynchronous=True, token="tok")
    endpoint = NegotiatedEndpoint("http://insecure.test/files/x", TOKEN_METHOD)
    with pytest.raises(OSError, match="https"):
        await fs._byte_send(endpoint, "GET")
    await fs.aclose()


async def test_byte_send_certificate_endpoint_requires_https(
    router: respx.Router,
) -> None:
    fs = make_fs(router, asynchronous=True, certfile="/tmp/p.pem")  # noqa: S108
    endpoint = NegotiatedEndpoint("http://insecure.test/files/x", CERTIFICATE_METHOD)
    with pytest.raises(OSError, match="https"):
        await fs._byte_send(endpoint, "GET")
    await fs.aclose()


async def test_byte_send_rejects_redirect(router: respx.Router) -> None:
    router.get(f"{BASE_URL}/files/x").mock(
        return_value=httpx.Response(302, headers={"Location": "https://evil.test/"}),
    )
    fs = make_fs(router, asynchronous=True)
    endpoint = NegotiatedEndpoint(f"{BASE_URL}/files/x", ANONYMOUS_METHOD)
    with pytest.raises(OSError, match="redirect"):
        await fs._byte_send(endpoint, "GET")
    await fs.aclose()


async def test_bearer_not_leaked_to_cross_origin_redirect(router: respx.Router) -> None:
    seen: dict[str, str | None] = {}
    mock_capabilities(router)
    router.get(NODES_URL).mock(return_value=httpx.Response(200, content=ROOT))
    cross = "https://evil.test/details"
    router.post(SYNC_URL).mock(
        return_value=httpx.Response(303, headers={"Location": cross}),
    )

    def capture(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, content=_details(f"{BASE_URL}/files/x"))

    router.get(cross).side_effect = capture
    fs = make_fs(router, asynchronous=True, token="secret-token")
    await fs._negotiate(
        "/file.txt", direction=DIRECTION_PULL, protocol_uri=PROTOCOL_HTTPS_GET
    )
    # The bearer reaches the same-origin POST but never the cross-origin details GET.
    assert seen["auth"] is None
    await fs.aclose()


def test_negotiate_module_exposes_directions() -> None:
    assert negotiate.DIRECTION_PUSH == "pushToVoSpace"
