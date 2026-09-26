import logging
import signal

import pytest


@pytest.fixture(autouse=True)
def _restore_process_state():
    """cli.main() replaces root log handlers and SIGINT/SIGTERM handlers; undo after each test."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    signals = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
    for sig, handler in signals.items():
        signal.signal(sig, handler)
