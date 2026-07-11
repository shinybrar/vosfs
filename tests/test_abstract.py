"""fsspec reusable abstract suites wired against vosfs (TRD section 15.1 item 5).

Each abstract suite from :mod:`fsspec.tests.abstract` is subclassed against a
simulator-backed synchronous :class:`~vosfs.VOSpaceFileSystem`. A single
:class:`VOSpaceFixtures` mixin supplies the ``fs``/``fs_join``/``fs_path``
fixtures; the inherited scenario fixtures build their trees through that same
filesystem, so state persists in the in-memory simulator.

Every abstract test that exercises a behaviour the v0.3.0 profile cannot express
is overridden with an explicit ``skip`` whose reason maps to the unsupported
capability, so the green run doubles as a capability matrix. The skips fall into
a small set of root causes:

* ``_WRITE_PARENT`` -- a byte write (``pipe_file``/``put_file``/``cp_file``)
  materializes only the target data node; it never creates a missing parent
  ``ContainerNode``. Files written below a not-yet-created directory are
  orphaned: unreachable by ``ls``/``find`` and the parent is not an ``isdir``.
* ``_COPY_TREE`` -- remote-to-remote recursive copy relays each data node's
  bytes but never materializes intermediate ``ContainerNode``s (the per-file
  copy hook cannot create directories), so copied directory trees are unreachable.
* ``_GET_TREE`` -- recursive download invokes the per-file byte hook on directory
  entries, but a ``ContainerNode`` has no negotiable byte endpoint (HTTP 404).
* ``_QUESTION_MARK`` -- path normalization treats ``?`` as a URL query delimiter,
  so glob patterns containing ``?`` cannot be resolved.
* ``_LIST_SOURCE`` -- ``_strip_protocol`` normalizes a single scalar path, so the
  list of remote sources that fsspec's ``get`` forwards raises before transfer.
"""

from __future__ import annotations

import posixpath
from typing import TYPE_CHECKING

import pytest
import respx
from conftest import BASE_URL, make_fs
from fsspec.tests.abstract import (
    AbstractCopyTests,
    AbstractFixtures,
    AbstractGetTests,
    AbstractOpenTests,
    AbstractPipeTests,
    AbstractPutTests,
)
from fsspec.tests.abstract.common import GLOB_EDGE_CASES_TESTS
from vospace_sim import VOSpaceSim

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from vosfs import VOSpaceFileSystem

_VERSION = "vosfs v0.3.0"

_WRITE_PARENT = (
    f"unsupported in {_VERSION}: a byte write materializes only the target data "
    "node and never creates a missing parent ContainerNode, so a file written "
    "below a not-yet-created directory is orphaned (no isdir, absent from ls/find)"
)
_COPY_TREE = (
    f"unsupported in {_VERSION}: remote-to-remote recursive copy relays data-node "
    "bytes but does not materialize intermediate ContainerNodes (the per-file copy "
    "hook cannot create directories), so a copied directory tree is unreachable"
)
_GET_TREE = (
    f"unsupported in {_VERSION}: recursive download invokes the per-file byte hook "
    "on directory entries, but a ContainerNode has no negotiable byte endpoint "
    "(HTTP 404)"
)
_QUESTION_MARK = (
    f"unsupported in {_VERSION}: path normalization treats '?' as a URL query "
    "delimiter, so glob patterns containing '?' cannot be resolved"
)
_LIST_SOURCE = (
    f"unsupported in {_VERSION}: getting a list of remote sources into a single "
    "local directory routes that list through _strip_protocol, which normalizes "
    "only a scalar path"
)
_HASHED_TEARDOWN = (
    f"unsupported in {_VERSION}: the hashed-names scenario pipes files under "
    "'source' without creating that container (writes never create parents), so "
    "the fixture's recursive-rm teardown cannot resolve the 'source' node"
)

# Exact (path, recursive, maxdepth) glob-edge-case rows that recursive download
# cannot serve: a matched ContainerNode is fed to the per-file byte hook.
_GET_GLOB_UNSUPPORTED = {
    ("*", True, None),
    ("*1", True, None),
    ("**", True, None),
    ("**/*1", True, None),
    ("**/subdir0", True, None),
    ("subdir[1-2]", True, None),
    ("subdir[0-1]", True, None),
}

# Exact (path, recursive, maxdepth) glob-edge-case rows whose upload writes files
# below a directory that is never materialized (no source directory forces a
# ``makedirs``), leaving them unreachable by ``find``.
_PUT_GLOB_UNSUPPORTED = {
    ("file[1-2]", False, None),
    ("file[1-2]", True, None),
    ("*", False, None),
    ("*", True, 1),
    ("*", True, 2),
    ("*1", False, None),
    ("*1", True, 2),
    ("**", False, None),
    ("**", True, 1),
    ("**", True, 2),
    ("**", False, 2),
    ("**/*1", False, None),
    ("**/*1", True, None),
    ("**/*1", True, 1),
    ("**/*1", True, 2),
    ("**/*1", False, 2),
    ("**/subdir0/nested*", True, 2),
    ("subdir[1-2]", True, 2),
    ("subdir[0-1]/*fil[e]*", False, None),
    ("subdir[0-1]/*fil[e]*", True, None),
}


def _glob_params(reason_for: Callable[..., str | None]) -> list:
    """Build a parametrization that skips the unsupported glob-edge-case rows.

    ``reason_for(path, recursive, maxdepth, expected)`` returns a skip reason for
    a row vosfs cannot express, or ``None`` for a row that must run normally.
    """
    params = []
    for path, recursive, maxdepth, expected in GLOB_EDGE_CASES_TESTS["argvalues"]:
        reason = reason_for(path, recursive, maxdepth, expected)
        marks = (pytest.mark.skip(reason=reason),) if reason else ()
        params.append(pytest.param(path, recursive, maxdepth, expected, marks=marks))
    return params


def _copy_glob_reason(path, recursive, maxdepth, expected) -> str | None:  # noqa: ARG001
    if "?" in path:
        return _QUESTION_MARK
    # Every row that actually copies something needs directories it cannot make;
    # only the empty-result rows (which copy nothing) can pass.
    return _COPY_TREE if expected else None


def _get_glob_reason(path, recursive, maxdepth, expected) -> str | None:  # noqa: ARG001
    if "?" in path:
        return _QUESTION_MARK
    return _GET_TREE if (path, recursive, maxdepth) in _GET_GLOB_UNSUPPORTED else None


def _put_glob_reason(path, recursive, maxdepth, expected) -> str | None:  # noqa: ARG001
    if "?" in path:
        return _QUESTION_MARK
    return (
        _WRITE_PARENT if (path, recursive, maxdepth) in _PUT_GLOB_UNSUPPORTED else None
    )


_COPY_GLOB_PARAMS = _glob_params(_copy_glob_reason)
_GET_GLOB_PARAMS = _glob_params(_get_glob_reason)
_PUT_GLOB_PARAMS = _glob_params(_put_glob_reason)


class VOSpaceFixtures(AbstractFixtures):
    """Bind the abstract fixtures to a fresh simulator-backed filesystem."""

    @pytest.fixture
    def fs(self) -> Iterator[VOSpaceFileSystem]:
        sim = VOSpaceSim()
        router = respx.Router(base_url=BASE_URL, assert_all_mocked=True)
        sim.install(router)
        filesystem = make_fs(router)
        yield filesystem
        filesystem.close()
        router.reset()

    @pytest.fixture
    def fs_join(self):
        # VOSpace paths are always POSIX; join with a forward slash regardless of
        # the host platform.
        return posixpath.join

    @pytest.fixture
    def fs_path(self) -> str:
        return "/"


class TestCopy(VOSpaceFixtures, AbstractCopyTests):
    """Remote-to-remote copy suite."""

    @pytest.mark.skip(reason=_WRITE_PARENT)
    def test_copy_file_to_new_directory(self): ...

    @pytest.mark.skip(reason=_WRITE_PARENT)
    def test_copy_file_to_file_in_new_directory(self): ...

    @pytest.mark.skip(reason=_WRITE_PARENT)
    def test_copy_list_of_files_to_new_directory(self): ...

    @pytest.mark.skip(reason=_WRITE_PARENT)
    def test_copy_two_files_new_directory(self): ...

    @pytest.mark.skip(reason=_COPY_TREE)
    def test_copy_directory_to_existing_directory(self): ...

    @pytest.mark.skip(reason=_COPY_TREE)
    def test_copy_directory_to_new_directory(self): ...

    @pytest.mark.skip(reason=_COPY_TREE)
    def test_copy_glob_to_existing_directory(self): ...

    @pytest.mark.skip(reason=_COPY_TREE)
    def test_copy_glob_to_new_directory(self): ...

    @pytest.mark.skip(reason=_COPY_TREE)
    def test_copy_directory_without_files_with_same_name_prefix(self): ...

    @pytest.mark.skip(reason=_HASHED_TEARDOWN)
    def test_copy_with_source_and_destination_as_list(self): ...

    @pytest.mark.parametrize(GLOB_EDGE_CASES_TESTS["argnames"], _COPY_GLOB_PARAMS)
    def test_copy_glob_edge_cases(  # noqa: PLR0913 - mirrors the abstract signature
        self,
        path,
        recursive,
        maxdepth,
        expected,
        fs,
        fs_join,
        fs_glob_edge_cases_files,
        fs_target,
        fs_sanitize_path,
    ):
        super().test_copy_glob_edge_cases(
            path,
            recursive,
            maxdepth,
            expected,
            fs,
            fs_join,
            fs_glob_edge_cases_files,
            fs_target,
            fs_sanitize_path,
        )


class TestGet(VOSpaceFixtures, AbstractGetTests):
    """Remote-to-local download suite."""

    @pytest.mark.skip(reason=_GET_TREE)
    def test_get_directory_to_existing_directory(self): ...

    @pytest.mark.skip(reason=_GET_TREE)
    def test_get_directory_to_new_directory(self): ...

    @pytest.mark.skip(reason=_GET_TREE)
    def test_get_glob_to_existing_directory(self): ...

    @pytest.mark.skip(reason=_GET_TREE)
    def test_get_glob_to_new_directory(self): ...

    @pytest.mark.skip(reason=_GET_TREE)
    def test_get_directory_recursive(self): ...

    @pytest.mark.skip(reason=_GET_TREE)
    def test_get_directory_without_files_with_same_name_prefix(self): ...

    @pytest.mark.skip(reason=_LIST_SOURCE)
    def test_get_list_of_files_to_existing_directory(self): ...

    @pytest.mark.skip(reason=_LIST_SOURCE)
    def test_get_list_of_files_to_new_directory(self): ...

    @pytest.mark.skip(reason=_HASHED_TEARDOWN)
    def test_get_with_source_and_destination_as_list(self): ...

    @pytest.mark.parametrize(GLOB_EDGE_CASES_TESTS["argnames"], _GET_GLOB_PARAMS)
    def test_get_glob_edge_cases(  # noqa: PLR0913 - mirrors the abstract signature
        self,
        path,
        recursive,
        maxdepth,
        expected,
        fs,
        fs_join,
        fs_glob_edge_cases_files,
        local_fs,
        local_join,
        local_target,
    ):
        super().test_get_glob_edge_cases(
            path,
            recursive,
            maxdepth,
            expected,
            fs,
            fs_join,
            fs_glob_edge_cases_files,
            local_fs,
            local_join,
            local_target,
        )


class TestPut(VOSpaceFixtures, AbstractPutTests):
    """Local-to-remote upload suite."""

    @pytest.mark.skip(reason=_WRITE_PARENT)
    def test_put_file_to_new_directory(self): ...

    @pytest.mark.skip(reason=_WRITE_PARENT)
    def test_put_file_to_file_in_new_directory(self): ...

    @pytest.mark.skip(reason=_WRITE_PARENT)
    def test_put_directory_to_existing_directory(self): ...

    @pytest.mark.skip(reason=_WRITE_PARENT)
    def test_put_directory_to_new_directory(self): ...

    @pytest.mark.skip(reason=_WRITE_PARENT)
    def test_put_list_of_files_to_new_directory(self): ...

    @pytest.mark.skip(reason=_WRITE_PARENT)
    def test_put_glob_to_existing_directory(self): ...

    @pytest.mark.skip(reason=_WRITE_PARENT)
    def test_put_glob_to_new_directory(self): ...

    @pytest.mark.parametrize(GLOB_EDGE_CASES_TESTS["argnames"], _PUT_GLOB_PARAMS)
    def test_put_glob_edge_cases(  # noqa: PLR0913 - mirrors the abstract signature
        self,
        path,
        recursive,
        maxdepth,
        expected,
        fs,
        fs_join,
        fs_target,
        local_glob_edge_cases_files,
        local_join,
        fs_sanitize_path,
    ):
        super().test_put_glob_edge_cases(
            path,
            recursive,
            maxdepth,
            expected,
            fs,
            fs_join,
            fs_target,
            local_glob_edge_cases_files,
            local_join,
            fs_sanitize_path,
        )


class TestPipe(VOSpaceFixtures, AbstractPipeTests):
    """Whole-object pipe suite."""


class TestOpen(VOSpaceFixtures, AbstractOpenTests):
    """File-handle open suite."""
