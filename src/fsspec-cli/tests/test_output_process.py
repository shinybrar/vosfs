"""Process-boundary evidence for plain ``ls`` output behavior."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ._process_support import (
    _REPO_ROOT,
    _TIMEOUT,
    _environment,
    _run_pty,
    _run_redirected,
)

_NATIVE_NEWLINE = os.linesep.encode()
_EXPECTED_TTY_STDOUT = b"\x1b[31mred\x1b[0m" + _NATIVE_NEWLINE
_OUTPUT_ERROR = (
    b"ls: output: output failure (OSError): disk\\\\bad\\x0aline" + _NATIVE_NEWLINE
)
_CHILD_PATH = Path(__file__).with_name("_output_process_child.py")


def _command(mode: str) -> list[str]:
    operands = ["memory:/docs"]
    if mode == "runtime-and-fail":
        operands = ["memory:/missing", *operands]
    return [sys.executable, str(_CHILD_PATH), mode, "ls", *operands]


def test_public_seam_broken_pipe_is_silent_runtime_failure() -> None:
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    try:
        result = subprocess.run(  # noqa: S603 - fixed child command.
            _command("normal"),
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


def test_public_seam_tty_matches_redirected_output_verbatim() -> None:
    redirected = _run_redirected(_command("tty"))
    returncode, stdout, stderr = _run_pty(_command("tty"))

    assert returncode == redirected.returncode == 0
    assert stdout == redirected.stdout == _EXPECTED_TTY_STDOUT
    assert stderr == redirected.stderr == b""


@pytest.mark.parametrize(
    ("mode", "expected_stdout"),
    [
        pytest.param("fail", b"", id="nothing-accepted"),
        pytest.param(
            "prefix",
            b"a.txt" + _NATIVE_NEWLINE,
            id="accepted-prefix-preserved",
        ),
    ],
)
def test_public_seam_reports_other_stdout_failures(
    mode: str,
    expected_stdout: bytes,
) -> None:
    result = _run_redirected(_command(mode))

    assert result.returncode == 1
    assert result.stdout == expected_stdout
    assert result.stderr == _OUTPUT_ERROR


def test_output_failure_keeps_already_known_backend_diagnostics() -> None:
    result = _run_redirected(_command("runtime-and-fail"))

    assert result.returncode == 1
    assert result.stdout == b""
    assert result.stderr == (
        b"ls: memory:/missing: not found" + _NATIVE_NEWLINE + _OUTPUT_ERROR
    )
