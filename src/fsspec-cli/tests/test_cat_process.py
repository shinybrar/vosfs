"""Process-boundary evidence for mapped-file ``cat`` binary output."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TIMEOUT = 5
_NATIVE_NEWLINE = os.linesep.encode()
_OUTPUT_ERROR = (
    b"cat: output: output failure (OSError): disk\\\\bad\\nline" + _NATIVE_NEWLINE
)
_CHILD_PATH = Path(__file__).with_name("_cat_process_child.py")


def _command(mode: str, *operands: str) -> list[str]:
    return [sys.executable, str(_CHILD_PATH), mode, "cat", *operands]


def _environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "LANG": "C",
            "LC_ALL": "C",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
        }
    )
    return environment


def _run_redirected(
    mode: str,
    *operands: str,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # noqa: S603 - fixed interpreter and child source.
        _command(mode, *operands),
        cwd=_REPO_ROOT,
        env=_environment(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=_TIMEOUT,
        check=False,
    )


def test_public_seam_cat_emits_all_byte_values() -> None:
    result = _run_redirected("bytes", "memory:/bytes")

    assert result.returncode == 0
    assert result.stdout == bytes(range(256))
    assert result.stderr == b""


def test_public_seam_cat_emits_empty_content() -> None:
    result = _run_redirected("empty", "memory:/empty")

    assert result.returncode == 0
    assert result.stdout == b""
    assert result.stderr == b""


def test_public_seam_cat_broken_pipe_is_silent_runtime_failure() -> None:
    if os.name != "posix":
        pytest.skip("closed-reader pipe evidence requires POSIX")

    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    try:
        result = subprocess.run(  # noqa: S603 - fixed child command.
            _command("normal", "memory:/docs"),
            cwd=_REPO_ROOT,
            env=_environment(),
            stdin=subprocess.DEVNULL,
            stdout=write_fd,
            stderr=subprocess.PIPE,
            timeout=_TIMEOUT,
            check=False,
        )
    finally:
        os.close(write_fd)

    assert result.returncode == 1
    assert result.stderr == b""


@pytest.mark.parametrize(
    ("mode", "operands", "expected_stdout"),
    [
        pytest.param("fail", ("memory:/prefix",), b"", id="nothing-accepted"),
        pytest.param(
            "prefix",
            ("memory:/prefix",),
            b"abc",
            id="accepted-prefix-preserved",
        ),
    ],
)
def test_public_seam_cat_reports_other_stdout_failures(
    mode: str,
    operands: tuple[str, ...],
    expected_stdout: bytes,
) -> None:
    result = _run_redirected(mode, *operands)

    assert result.returncode == 1
    assert result.stdout == expected_stdout
    assert result.stderr == _OUTPUT_ERROR


def test_cat_output_failure_keeps_already_known_backend_diagnostics() -> None:
    result = _run_redirected("runtime-and-fail", "memory:/missing", "memory:/docs")

    assert result.returncode == 1
    assert result.stdout == b""
    assert result.stderr == (
        b"cat: memory:/missing: not found" + _NATIVE_NEWLINE + _OUTPUT_ERROR
    )
