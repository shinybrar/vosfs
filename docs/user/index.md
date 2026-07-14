# vosfs

`vosfs` is an asynchronous [`fsspec`](https://filesystem-spec.readthedocs.io/)
filesystem for the OpenCADC Cavern VOSpace service. It registers the `vos`
protocol so fsspec-aware Python tools — and a bounded set of scientific-stack
consumers (pandas, NumPy, Dask, Zarr, PyArrow) — can read, write, inspect, and
mutate OpenCADC VOSpace paths.

`vosfs` targets the OpenCADC VOSpace profile only; it does not claim generic
IVOA VOSpace 2.1 conformance.

## Install

```bash
uv add git+https://github.com/shinybrar/vosfs@main
```

## Authenticated quickstart

Set exactly one credential source. A CADC proxy certificate is a combined
certificate-chain and private-key PEM file; use an absolute path because shell
shortcuts such as `~` are not expanded by Python libraries.

```bash
export VOSFS_CERT_FILE=/absolute/path/to/cadcproxy.pem
```

The normal synchronous fsspec API is available even though `vosfs` implements
network I/O asynchronously.

```python
import fsspec

fs = fsspec.filesystem("vos", endpoint_url="https://staging.canfar.net/arc")
try:
    print(fs.ls("/home/<cadc-username>"))
finally:
    fs.close()
```

- The [User Guide](guide.md) covers construction, credentials, and the
  supported and unsupported behavior.
- The [API Reference](api-reference.md) documents the public interface.
- The normative surface is the capability contract in
  [`docs/design/trd.md`](https://github.com/shinybrar/vosfs/blob/main/docs/design/trd.md).

To contribute, follow the
[contributor guide](https://github.com/shinybrar/vosfs/blob/main/CONTRIBUTING.md).
