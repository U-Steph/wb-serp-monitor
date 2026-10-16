"""StopFlag and install_stop_handlers: set by the signal, handlers restored, no-op off-thread."""

from __future__ import annotations

import signal
import threading

import pytest

from wbmon.signals import StopFlag, install_stop_handlers


def test_flag_starts_clear_and_records_the_signal():
    flag = StopFlag()
    assert flag.requested is False
    assert flag.signum is None
    flag.request(signal.SIGTERM)
    assert flag.requested is True
    assert flag.signum == signal.SIGTERM


def test_request_without_a_signal_number():
    flag = StopFlag()
    flag.request()
    assert flag.requested is True
    assert flag.signum is None


def test_sigterm_sets_the_flag_and_the_handler_is_restored():
    before = signal.getsignal(signal.SIGTERM)
    flag = StopFlag()
    with install_stop_handlers(flag):
        assert signal.getsignal(signal.SIGTERM) is not before
        signal.raise_signal(signal.SIGTERM)
        assert flag.requested is True
        assert flag.signum == signal.SIGTERM
    assert signal.getsignal(signal.SIGTERM) == before


def test_handler_is_restored_when_the_block_raises():
    before = signal.getsignal(signal.SIGTERM)
    with pytest.raises(RuntimeError, match="boom"), install_stop_handlers(StopFlag()):
        raise RuntimeError("boom")
    assert signal.getsignal(signal.SIGTERM) == before


def test_sigterm_and_sigint_are_both_handled():
    before = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    flag = StopFlag()
    with install_stop_handlers(flag, (signal.SIGTERM, signal.SIGINT)):
        signal.raise_signal(signal.SIGINT)
        assert flag.requested and flag.signum == signal.SIGINT
        flag.requested, flag.signum = False, None
        signal.raise_signal(signal.SIGTERM)
        assert flag.requested and flag.signum == signal.SIGTERM
    assert {s: signal.getsignal(s) for s in before} == before


def test_default_installs_sigterm_only():
    sigint_before = signal.getsignal(signal.SIGINT)
    with install_stop_handlers(StopFlag()):
        assert signal.getsignal(signal.SIGINT) == sigint_before


def test_noop_outside_the_main_thread():
    before = signal.getsignal(signal.SIGTERM)
    flag = StopFlag()
    seen: dict[str, object] = {}

    def worker() -> None:
        try:
            with install_stop_handlers(flag) as yielded:
                seen["inside"] = signal.getsignal(signal.SIGTERM)
                seen["flag"] = yielded
        except BaseException as exc:  # noqa: BLE001 - report to the main thread
            seen["error"] = exc

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(10)
    assert "error" not in seen
    assert seen["inside"] == before
    assert seen["flag"] is flag
    assert flag.requested is False
    assert signal.getsignal(signal.SIGTERM) == before
