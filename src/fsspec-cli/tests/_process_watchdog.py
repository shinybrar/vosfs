"""Self-termination watchdog for process-boundary subprocess fixtures.

The parent tests launch these children with ``subprocess.run(..., timeout=5)``.
On Windows a wedged child froze the whole pytest session: after the parent's
timeout fired it killed the direct child, then drained the pipes with an
untimed ``communicate()`` that blocked forever waiting on reader threads for
handles the killed child never closed.

Arming this watchdog makes the child terminate itself just under the parent's
timeout, so the parent's ``communicate`` returns normally and never enters the
untimed drain. A would-be hang becomes a fast, diagnosable failure: the child
dumps every thread's stack before exiting non-zero.
"""

from __future__ import annotations

import faulthandler
import os
import sys
import threading

# Comfortably below the parent's 5-second subprocess timeout so the child
# always exits (closing its pipe handles) before the parent waits on them.
_WATCHDOG_SECONDS = 4.0
_EXIT_CODE = 3


def _abort() -> None:
    stream = sys.__stderr__
    if stream is not None:
        stream.write("process-child watchdog expired; dumping stacks\n")
        stream.flush()
        faulthandler.dump_traceback(file=stream, all_threads=True)
        stream.flush()
    os._exit(_EXIT_CODE)


def arm() -> None:
    """Start a daemon timer that force-exits a wedged child with a stack dump."""
    timer = threading.Timer(_WATCHDOG_SECONDS, _abort)
    timer.daemon = True
    timer.start()
