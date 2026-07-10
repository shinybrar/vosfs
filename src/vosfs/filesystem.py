"""The public ``vos`` fsspec filesystem for the OpenCADC VOSpace profile.

This module hosts :class:`VOSpaceFileSystem`, the single public class of the
package. Behaviour is layered across cohesive internal modules (paths,
configuration, transport, and so on); this class composes them into the fsspec
async filesystem contract.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import httpx
from fsspec.asyn import AsyncFileSystem

from vosfs import config, paths

if TYPE_CHECKING:
    from collections.abc import Mapping


class VOSpaceFileSystem(AsyncFileSystem):
    """An asynchronous fsspec filesystem for the OpenCADC VOSpace profile.

    The service is selected only by ``endpoint_url``. The apparent authority
    after the ``vos://`` protocol marker is part of the filesystem path, not a
    service or VOSpace authority.
    """

    protocol = "vos"
    async_impl = True
    cachable = True
    # ``transport`` is the single internal HTTP transport seam and ``loop`` is a
    # runtime handle; neither is part of the serialized ``storage_options``.
    _strip_tokenize_options = ("transport", "loop")

    def __init__(  # noqa: PLR0913 - the public transport options are the contract (section 3)
        self,
        endpoint_url: str,
        *,
        token: str | None = None,
        tokenfile: str | None = None,
        certfile: str | None = None,
        timeouts: Mapping[str, float] | None = None,
        trust_env: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
        asynchronous: bool = False,
        loop: Any = None,  # noqa: ANN401 - fsspec passes an opaque event loop
        batch_size: int | None = None,
        **storage_options: Any,  # noqa: ANN401 - fsspec passthrough options
    ) -> None:
        """Construct a filesystem bound to one OpenCADC VOSpace deployment.

        Args:
            endpoint_url: Absolute OpenCADC service base URL.
            token: Literal bearer token, including an OIDC access token.
            tokenfile: File whose bearer token is reread before every request.
            certfile: Combined X.509 certificate-chain and private-key PEM file.
            timeouts: Optional finite positive ``connect``/``read``/``write``/
                ``pool`` inactivity limits.
            trust_env: Whether HTTPX honours proxy and CA environment handling.
            transport: Test-only HTTP transport injected at the internal seam.
                Never appears in ``storage_options``.
            asynchronous: fsspec async-mode flag.
            loop: fsspec event loop handle; not serialized.
            batch_size: fsspec bulk-operation concurrency hint.
            storage_options: Remaining fsspec options (``skip_instance_cache``,
                ``use_listings_cache``, ``listings_expiry_time``, ``max_paths``).
        """
        config.reject_forbidden_options(storage_options)
        super().__init__(
            asynchronous=asynchronous,
            loop=loop,
            batch_size=batch_size,
            **storage_options,
        )
        self._credential = config.resolve_credential(
            token=token,
            tokenfile=tokenfile,
            certfile=certfile,
            environ=os.environ,
        )
        self.endpoint_url = config.validate_endpoint(
            endpoint_url,
            has_credential=not self._credential.is_anonymous,
        )
        self.timeouts = config.resolve_timeouts(timeouts)
        self.trust_env = trust_env
        self._injected_transport = transport

    @classmethod
    def _strip_protocol(cls, path: str) -> str:
        """Normalize a user path to the canonical internal VOSpace path."""
        return paths.strip_protocol(path)

    def _new_client(
        self,
        *,
        verify: bool = True,
        cert: Any = None,  # noqa: ANN401 - httpx cert spec, wired fully with the pool
    ) -> httpx.AsyncClient:
        """Build an HTTPX async client through the single transport seam.

        Production builds a real HTTPX transport; tests inject a mock transport
        via the ``transport`` constructor option. This is the one place HTTP
        transports are constructed.
        """
        kwargs: dict[str, Any] = {
            "follow_redirects": False,
            "trust_env": self.trust_env,
        }
        if self._injected_transport is not None:
            kwargs["transport"] = self._injected_transport
        else:
            kwargs["verify"] = verify
            if cert is not None:
                kwargs["cert"] = cert
        return httpx.AsyncClient(**kwargs)
