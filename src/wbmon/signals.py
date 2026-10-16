"""Stop-request flag and signal handler installation for long-running commands.

The handler only flips a plain bool: no logging, locks or ``threading.Event`` inside a signal
handler (they can deadlock when the handler interrupts the same thread). The main loop polls the
flag on short ticks.
"""

from __future__ import annotations

import contextlib
import signal
import threading
from collections.abc import Iterable, Iterator


class StopFlag:
    """A plain stop request: ``requested`` becomes True once and stays True."""

    def __init__(self) -> None:
        self.requested = False
        self.signum: int | None = None

    def request(self, signum: int | None = None) -> None:
        self.requested = True
        self.signum = signum


@contextlib.contextmanager
def install_stop_handlers(
    flag: StopFlag, signums: Iterable[int] = (signal.SIGTERM,)
) -> Iterator[StopFlag]:
    """Route ``signums`` to ``flag.request`` inside the block, restoring the old handlers after.

    ``signal.signal`` only works in the main thread; elsewhere this is a no-op.
    """
    if threading.current_thread() is not threading.main_thread():
        yield flag
        return
    wanted = tuple(signums)
    previous = {signum: signal.getsignal(signum) for signum in wanted}

    def _handler(signum: int, _frame: object) -> None:
        flag.request(signum)

    for signum in wanted:
        signal.signal(signum, _handler)
    try:
        yield flag
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler if handler is not None else signal.SIG_DFL)
