"""An asynchronous fsspec filesystem for the OpenCADC VOSpace profile.

``vosfs`` provides the ``vos`` protocol for fsspec-aware Python tools. See the
v0.3.0 capability contract in ``docs/design/trd.md`` for the normative surface.
The ``vos`` protocol is registered with fsspec through the ``fsspec.specs``
entry-point group declared in ``pyproject.toml``.
"""

from vosfs.filesystem import VOSpaceFileSystem

__all__ = ["VOSpaceFileSystem"]
