"""Read local CSV text or retry a remote source; preserve original encoding rules.

An optional opener supports the legacy BLB predictor's urlopen mock hook.
"""
from __future__ import annotations

import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def read_source(source: str, *, opener=urlopen) -> str:
    if not source.startswith(("https://", "http://")):
        return Path(source).read_text(encoding="utf-8-sig")
    for attempt in range(3):
        try:
            request = Request(source, headers={"Cache-Control": "no-cache"})
            with opener(request, timeout=90) as response:
                return response.read().decode("utf-8-sig")
        except (HTTPError, URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("Unreachable source retry state")
