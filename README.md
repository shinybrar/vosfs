# vosfs

`vosfs` is an asynchronous [`fsspec`](https://filesystem-spec.readthedocs.io/)
filesystem for the **OpenCADC Cavern VOSpace** service. It registers the `vos`
protocol so fsspec-aware Python tools — and pandas, NumPy, Dask, Zarr, and
PyArrow — can read, write, inspect, and mutate OpenCADC VOSpace paths.

```python
import fsspec

fs = fsspec.filesystem("vos", endpoint_url="https://staging.canfar.net/arc")
fs.ls("/")
```

`vosfs` targets the OpenCADC VOSpace profile only; it does not claim generic
IVOA VOSpace 2.1 conformance.

The [vosfs documentation](https://shinybrar.github.io/vosfs/) contains the User
Guide and public API reference. The normative surface is the capability contract
in [`docs/design/trd.md`](docs/design/trd.md). See
[CONTRIBUTING.md](CONTRIBUTING.md) to work on the project.

Public documentation source lives under `docs/user/`. Validate it locally with:

```bash
uv run zensical build --strict --clean
```
