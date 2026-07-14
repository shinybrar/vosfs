# vosfs

`vosfs` is an asynchronous [`fsspec`](https://filesystem-spec.readthedocs.io/)
filesystem for the **OpenCADC Cavern VOSpace** service. It registers the `vos`
protocol so fsspec-aware Python tools — and pandas, NumPy, Dask, Zarr, and
PyArrow — can read, write, inspect, and mutate OpenCADC VOSpace paths.

Install the current development version directly from GitHub:

```bash
uv add git+https://github.com/shinybrar/vosfs@main
```

The normal synchronous fsspec API is available even though network I/O is
implemented asynchronously. For an authenticated first request, point
`VOSFS_CERT_FILE` at a combined proxy-certificate and private-key PEM file:

```bash
export VOSFS_CERT_FILE=/absolute/path/to/cadcproxy.pem
```

```python
import fsspec

fs = fsspec.filesystem("vos", endpoint_url="https://staging.canfar.net/arc")
try:
    print(fs.ls("/home/<cadc-username>"))
finally:
    fs.close()
```

`vosfs` targets the OpenCADC VOSpace profile only; it does not claim generic
IVOA VOSpace 2.1 conformance.

The [vosfs documentation](https://shinybrar.github.io/vosfs/) contains the User
Guide, credential details, scientific-stack examples, and public API reference.
The normative surface is the capability contract in
[`docs/design/trd.md`](docs/design/trd.md). See
[CONTRIBUTING.md](CONTRIBUTING.md) to work on the project or run the live
OpenCADC integration gate.

Public documentation source lives under `docs/user/`. Validate it locally with:

```bash
uv run zensical build --strict --clean
```
