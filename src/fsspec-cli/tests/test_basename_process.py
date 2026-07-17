"""Process-boundary evidence for ``basename`` output behavior."""

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
_NATIVE_NEWLINE = os.linesep.encode()
_OPERAND = "dir\nname"
_CHILD_PATH = Path(__file__).with_name("_basename_process_child.py")


def _expected_stdout(text: str) -> bytes:
    return (f"{text}\n").replace("\n", os.linesep).encode()


_EXPECTED_STDOUT = _expected_stdout(_OPERAND)


def _command(*, suffix: str | None = None) -> list[str]:
    command = [sys.executable, str(_CHILD_PATH), _OPERAND]
    if suffix is not None:
        command.append(suffix)
    return command


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


def _run_redirected() -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # noqa: S603 - fixed interpreter and child source.
        _command(),
        cwd=_REPO_ROOT,
        env=_environment(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=_TIMEOUT,
        check=False,
    )


def _assert_redirected_output(
    command: list[str],
    *,
    expected_stdout: bytes,
) -> None:
    redirected = subprocess.run(  # noqa: S603 - fixed interpreter and child source.
        command,
        cwd=_REPO_ROOT,
        env=_environment(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=_TIMEOUT,
        check=False,
    )
    assert redirected.returncode == 0
    assert redirected.stdout == expected_stdout
    assert redirected.stderr == b""


def test_public_seam_repeated_suffix_matches_redirected_output_verbatim() -> None:
    operand = "foo.txt.txt"
    suffix = ".txt"
    expected_stdout = b"foo.txt" + _NATIVE_NEWLINE
    command = [sys.executable, str(_CHILD_PATH), operand, suffix]
    _assert_redirected_output(command, expected_stdout=expected_stdout)


def test_public_seam_embedded_newline_suffix_matches_redirected_output_verbatim() -> (
    None
):
    operand = "prefix\ntail"
    suffix = "\ntail"
    expected_stdout = b"prefix" + _NATIVE_NEWLINE
    command = [sys.executable, str(_CHILD_PATH), operand, suffix]
    _assert_redirected_output(command, expected_stdout=expected_stdout)


def test_public_seam_option_looking_suffix_matches_redirected_output_verbatim() -> None:
    operand = "foo-l"
    expected_stdout = b"foo" + _NATIVE_NEWLINE
    command = [sys.executable, str(_CHILD_PATH), operand, "--", "-l"]
    _assert_redirected_output(command, expected_stdout=expected_stdout)


def test_public_seam_suffix_tty_matches_redirected_output_verbatim() -> None:
    operand = "file.txt"
    suffix = ".txt"
    expected_stdout = b"file" + _NATIVE_NEWLINE
    command = [sys.executable, str(_CHILD_PATH), operand, suffix]

    redirected = subprocess.run(  # noqa: S603 - fixed interpreter and child source.
        command,
        cwd=_REPO_ROOT,
        env=_environment(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=_TIMEOUT,
        check=False,
    )
    if os.name != "posix":
        assert redirected.returncode == 0
        assert redirected.stdout == expected_stdout
        assert redirected.stderr == b""
        return

    returncode, stdout, stderr = _run_pty_command(command)

    assert returncode == redirected.returncode == 0
    assert stdout == redirected.stdout == expected_stdout
    assert stderr == redirected.stderr == b""


def _run_pty_command(command: list[str]) -> tuple[int, bytes, bytes]:
    if os.name != "posix":
        pytest.skip("PTY evidence requires POSIX")
    if not hasattr(termios, "ONLCR") or not hasattr(termios, "ECHO"):
        pytest.skip("required terminal flags unavailable")

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


def test_public_seam_tty_matches_redirected_output_verbatim() -> None:
    redirected = _run_redirected()
    if os.name != "posix":
        assert redirected.returncode == 0
        assert redirected.stdout == _EXPECTED_STDOUT
        assert redirected.stderr == b""
        return

    returncode, stdout, stderr = _run_pty_command(_command())

    assert returncode == redirected.returncode == 0
    assert stdout == redirected.stdout == _EXPECTED_STDOUT
    assert stderr == redirected.stderr == b""
