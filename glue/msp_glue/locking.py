"""One lock for every change msp-glue makes, shared by the service and the mspctl command.

The service handles webhooks one at a time, but an engineer may run mspctl inside the same
container while a webhook is being handled. Both take this lock, so two processes never
create the same host, asset or ticket at the same moment.
"""

from __future__ import annotations

import contextlib
import fcntl
import logging

from .settings import settings

log = logging.getLogger("msp_glue.lock")


@contextlib.contextmanager
def exclusive():
    with open(settings.lock_file, "a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.info("Waiting for msp-glue to finish its current job")
            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
