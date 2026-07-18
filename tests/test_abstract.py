"""fsspec reusable abstract suites wired against vosfs (TRD section 15.1 item 5).

Each abstract suite from :mod:`fsspec.tests.abstract` is subclassed against a
simulator-backed synchronous :class:`~vosfs.VOSpaceFileSystem`. A single
:class:`VOSpaceFixtures` mixin supplies the ``fs``/``fs_join``/``fs_path``
fixtures; the inherited scenario fixtures build their trees through that same
filesystem, so state persists in the in-memory simulator.

Every abstract test that exercises a behaviour the v0.3.0 profile cannot express
is overridden with an explicit ``skip`` whose reason maps to the unsupported
capability, so the green run doubles as a capability matrix. The remaining
skip root cause is ``_QUESTION_MARK``: path normalization treats ``?`` as a URL
query delimiter, so glob patterns containing ``?`` cannot be resolved.
"""

from __future__ import annotations

import posixpath
from importlib.metadata import version
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

# Derived from the installed package so skip reasons never drift from the
# shipped version. The unsupported capabilities they describe are fixed by the
# capability contract; the version label simply tracks the release under test.
_VERSION = f"vosfs v{version('vosfs')}"

_QUESTION_MARK = (
    f"unsupported in {_VERSION}: path normalization treats '?' as a URL query "
    "delimiter, so glob patterns containing '?' cannot be resolved"
)


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
    # Recursive copy now materializes intermediate ContainerNodes, so only the
    # '?'-glob rows (unresolvable path normalization) remain unsupported.
    return None


_QUESTION_MARK_GLOB_PARAMS = _glob_params(_copy_glob_reason)


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

    # Copying a file below a not-yet-created directory, and recursive/directory
    # copies, are supported: ``_cp_file`` creates the destination file's parent
    # and materializes intermediate ContainerNodes, so these inherited tests run.

    @pytest.mark.parametrize(
        GLOB_EDGE_CASES_TESTS["argnames"], _QUESTION_MARK_GLOB_PARAMS
    )
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

    # test_get_list_of_files_to_{existing,new}_directory now run: _strip_protocol
    # normalizes fsspec's forwarded list of sources (see the list branch on
    # VOSpaceFileSystem._strip_protocol).

    @pytest.mark.parametrize(
        GLOB_EDGE_CASES_TESTS["argnames"], _QUESTION_MARK_GLOB_PARAMS
    )
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

    @pytest.mark.parametrize(
        GLOB_EDGE_CASES_TESTS["argnames"], _QUESTION_MARK_GLOB_PARAMS
    )
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
