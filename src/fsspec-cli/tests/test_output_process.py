"""Process-boundary evidence for plain ``ls`` output behavior."""

import errno
import os
import subprocess
import sys
from contextlib import suppress
from pathlib import Path

import pytest

if os.name == "posix":
    import pty
    import termios

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TIMEOUT = 5
_EXPECTED_TTY_STDOUT = b"\x1b[31mred\x1b[0m\n"
_OUTPUT_ERROR = b"ls: output: output failure (OSError): disk\\\\bad\\nline\n"
_CHILD_PATH = Path(__file__).with_name("_output_process_child.py")


def _command(mode: str) -> list[str]:
    operands = ["memory:/docs"]
    if mode == "runtime-and-fail":
        operands = ["memory:/missing", *operands]
    return [sys.executable, str(_CHILD_PATH), mode, "ls", *operands]


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


def _run_redirected(mode: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # noqa: S603 - fixed interpreter and child source.
        _command(mode),
        cwd=_REPO_ROOT,
        env=_environment(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=_TIMEOUT,
        check=False,
    )


def _run_pty(mode: str) -> tuple[int, bytes, bytes]:
    if os.name != "posix":
        pytest.skip("PTY evidence requires POSIX")
    if not hasattr(termios, "ONLCR") or not hasattr(termios, "ECHO"):
        pytest.skip("required terminal flags unavailable")

    command = _command(mode)
    master_fd, slave_fd = pty.openpty()
    try:
        attributes = termios.tcgetattr(slave_fd)
        attributes[1] &= ~termios.ONLCR
        attributes[3] &= ~termios.ECHO
        termios.tcsetattr(slave_fd, termios.TCSANOW, attributes)

        result = subprocess.run(  # noqa: S603 - fixed child command.
            command,
            cwd=_REPO_ROOT,
            env=_environment(),
            stdin=subprocess.DEVNULL,
            stdout=slave_fd,
            stderr=subprocess.PIPE,
            timeout=_TIMEOUT,
            check=False,
        )
        chunks = [os.read(master_fd, 65536)]
        os.close(slave_fd)
        slave_fd = -1

        while True:
            try:
                chunk = os.read(master_fd, 65536)
            except OSError as error:
                if error.errno == errno.EIO:
                    break
                raise
            if not chunk:
                break
            chunks.append(chunk)
        return result.returncode, b"".join(chunks), result.stderr
    finally:
        for descriptor in (master_fd, slave_fd):
            if descriptor >= 0:
                with suppress(OSError):
                    os.close(descriptor)


def test_public_seam_broken_pipe_is_silent_runtime_failure() -> None:
    if os.name != "posix":
        pytest.skip("closed-reader pipe evidence requires POSIX")

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
    redirected = _run_redirected("tty")
    returncode, stdout, stderr = _run_pty("tty")

    assert returncode == redirected.returncode == 0
    assert stdout == redirected.stdout == _EXPECTED_TTY_STDOUT
    assert stderr == redirected.stderr == b""


@pytest.mark.parametrize(
    ("mode", "expected_stdout"),
    [
        pytest.param("fail", b"", id="nothing-accepted"),
        pytest.param("prefix", b"a.txt\n", id="accepted-prefix-preserved"),
    ],
)
def test_public_seam_reports_other_stdout_failures(
    mode: str,
    expected_stdout: bytes,
) -> None:
    result = _run_redirected(mode)

    assert result.returncode == 1
    assert result.stdout == expected_stdout
    assert result.stderr == _OUTPUT_ERROR


def test_output_failure_keeps_already_known_backend_diagnostics() -> None:
    result = _run_redirected("runtime-and-fail")

    assert result.returncode == 1
    assert result.stdout == b""
    assert result.stderr == (b"ls: memory:/missing: not found\n" + _OUTPUT_ERROR)
