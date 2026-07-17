"""Process-boundary evidence for ``dirname`` output behavior."""

import errno
import os
import pty
import subprocess
import sys
import termios
from contextlib import suppress
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TIMEOUT = 5
_NATIVE_NEWLINE = os.linesep.encode()
_OPERAND = "a\n/b"
_EXPECTED_STDOUT = b"a\n" + _NATIVE_NEWLINE
_CHILD_PATH = Path(__file__).with_name("_dirname_process_child.py")


def _command() -> list[str]:
    return [sys.executable, str(_CHILD_PATH), _OPERAND]


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


def _run_pty() -> tuple[int, bytes, bytes]:
    command = _command()
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
    returncode, stdout, stderr = _run_pty()

    assert returncode == redirected.returncode == 0
    assert stdout == redirected.stdout == _EXPECTED_STDOUT
    assert stderr == redirected.stderr == b""
