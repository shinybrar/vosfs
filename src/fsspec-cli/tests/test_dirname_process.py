"""Process-boundary evidence for ``dirname`` output behavior."""

import os
import sys
from pathlib import Path

from ._process_support import _run_pty, _run_redirected

_NATIVE_NEWLINE = os.linesep.encode()
_OPERAND = "a\n/b"
_EXPECTED_STDOUT = b"a\n" + _NATIVE_NEWLINE
_CHILD_PATH = Path(__file__).with_name("_path_process_child.py")


def _command() -> list[str]:
    return [sys.executable, str(_CHILD_PATH), "dirname", _OPERAND]


def test_public_seam_tty_matches_redirected_output_verbatim() -> None:
    redirected = _run_redirected(_command())
    returncode, stdout, stderr = _run_pty(_command())

    assert returncode == redirected.returncode == 0
    assert stdout == redirected.stdout == _EXPECTED_STDOUT
    assert stderr == redirected.stderr == b""
