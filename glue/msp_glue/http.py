"""Shared HTTP session with sensible timeouts and retries for idempotent calls."""

from __future__ import annotations

import logging

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger("msp_glue.http")

DEFAULT_TIMEOUT = 30


class APIError(RuntimeError):
    """Raised when a remote API returns an error we cannot recover from."""


class NotFound(APIError):
    """Raised when the object asked for does not exist (HTTP 404)."""


def make_session(retries: int = 3) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=1.0,
        status_forcelist=(502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD", "OPTIONS"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session
