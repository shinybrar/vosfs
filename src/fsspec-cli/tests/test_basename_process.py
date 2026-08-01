"""Process-boundary evidence for ``basename`` output behavior."""

import os
import sys
from pathlib import Path

from ._process_support import _run_pty, _run_redirected

_NATIVE_NEWLINE = os.linesep.encode()
_OPERAND = "dir\nname"
_CHILD_PATH = Path(__file__).with_name("_path_process_child.py")


def _expected_stdout(text: str) -> bytes:
    return (f"{text}\n").replace("\n", os.linesep).encode()


_EXPECTED_STDOUT = _expected_stdout(_OPERAND)


def _command(*arguments: str) -> list[str]:
    return [sys.executable, str(_CHILD_PATH), "basename", *arguments]


def _assert_redirected_output(
    command: list[str],
    *,
    expected_stdout: bytes,
) -> None:
    redirected = _run_redirected(command)
    assert redirected.returncode == 0
    assert redirected.stdout == expected_stdout
    assert redirected.stderr == b""


def test_public_seam_repeated_suffix_matches_redirected_output_verbatim() -> None:
    expected_stdout = b"foo.txt" + _NATIVE_NEWLINE
    _assert_redirected_output(
        _command("foo.txt.txt", ".txt"),
        expected_stdout=expected_stdout,
    )


def test_public_seam_embedded_newline_suffix_matches_redirected_output_verbatim() -> (
    None
):
    expected_stdout = b"prefix" + _NATIVE_NEWLINE
    _assert_redirected_output(
        _command("prefix\ntail", "\ntail"),
        expected_stdout=expected_stdout,
    )


def test_public_seam_option_looking_suffix_matches_redirected_output_verbatim() -> None:
    expected_stdout = b"foo" + _NATIVE_NEWLINE
    _assert_redirected_output(
        _command("foo-l", "--", "-l"),
        expected_stdout=expected_stdout,
    )


def test_public_seam_suffix_tty_matches_redirected_output_verbatim() -> None:
    expected_stdout = b"file" + _NATIVE_NEWLINE
    command = _command("file.txt", ".txt")

    redirected = _run_redirected(command)
    returncode, stdout, stderr = _run_pty(command)

    assert returncode == redirected.returncode == 0
    assert stdout == redirected.stdout == expected_stdout
    assert stderr == redirected.stderr == b""


def test_public_seam_tty_matches_redirected_output_verbatim() -> None:
    command = _command(_OPERAND)
    redirected = _run_redirected(command)
    returncode, stdout, stderr = _run_pty(command)

    assert returncode == redirected.returncode == 0
    assert stdout == redirected.stdout == _EXPECTED_STDOUT
    assert stderr == redirected.stderr == b""
