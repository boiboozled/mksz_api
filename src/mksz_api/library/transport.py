"""How endpoints get their JSON: a Transport turns a PageRequest into named payloads.

mksz.hu and livescore.mksz.hu are Vue apps that load all data as JSON after the page opens. The default
BrowserTransport opens the page in headless Chromium like a normal visitor and keeps the JSON responses
the page receives that match each Capture's pattern. Endpoints never build API calls themselves, so a
different transport (e.g. plain HTTP) can be plugged in without touching them.
"""

from __future__ import annotations

import atexit
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import unquote


class TransportError(RuntimeError):
    pass


class CaptureTimeout(TransportError):
    """The page loaded but some expected payloads never arrived."""


@dataclass(frozen=True)
class Capture:
    name: str
    pattern: str  # regex searched in the URL-decoded response URL
    api_path: str | None = None  # the API path the page calls; informational, for non-browser transports


@dataclass
class PageRequest:
    url: str
    captures: list[Capture]
    # JavaScript arrow function run repeatedly after load until it returns true. Used to drive the page's
    # own controls (season select, program date range), so the page itself requests the data we want.
    script: str | None = None
    timeout: float | None = None  # seconds; None = the transport's default
    extra: dict[str, Any] = field(default_factory=dict)


class Transport(Protocol):
    def fetch(self, request: PageRequest) -> dict[str, Any]:
        """Return {capture.name: payload} for every capture of the request, or raise TransportError."""
        ...


class BrowserTransport:
    """Headless Chromium via Playwright. One browser is shared by all requests; pages are loaded at most
    one per `delay` seconds to stay polite."""

    def __init__(self, delay: float = 2.0, timeout: float = 45.0, headless: bool = True, retries: int = 1):
        self.delay = delay
        self.timeout = timeout
        self.headless = headless
        self.retries = retries
        self._pw = None
        self._browser = None
        self._context = None
        self._last = 0.0

    def _ensure(self):
        if self._context is None:
            from playwright.sync_api import sync_playwright

            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=self.headless)
            self._context = self._browser.new_context(locale="hu-HU")
            atexit.register(self.close)
        return self._context

    def close(self) -> None:
        for obj, meth in ((self._context, "close"), (self._browser, "close"), (self._pw, "stop")):
            if obj is not None:
                try:
                    getattr(obj, meth)()
                except Exception:  # noqa: BLE001 - best effort at shutdown
                    pass
        self._pw = self._browser = self._context = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def fetch(self, request: PageRequest) -> dict[str, Any]:
        last_error: Exception | None = None
        for _ in range(self.retries + 1):
            try:
                return self._fetch_once(request)
            except TransportError as e:
                last_error = e
            except Exception as e:  # playwright errors (navigation timeouts, crashed page, ...)
                last_error = TransportError(f"{request.url}: {e}")
        raise last_error  # type: ignore[misc]

    def _fetch_once(self, request: PageRequest) -> dict[str, Any]:
        wait = self.delay - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        timeout = request.timeout or self.timeout
        got: dict[str, Any] = {}
        patterns = [(c.name, re.compile(c.pattern)) for c in request.captures]

        def on_response(resp):
            url = unquote(resp.url)
            for name, rx in patterns:
                if name not in got and rx.search(url):
                    try:
                        got[name] = resp.json()
                    except Exception:  # noqa: BLE001 - non-JSON body (error page); keep waiting
                        pass

        page = self._ensure().new_page()
        try:
            page.on("response", on_response)
            page.goto(request.url, wait_until="domcontentloaded", timeout=timeout * 1000)
            deadline = time.monotonic() + timeout
            script_done = request.script is None
            while time.monotonic() < deadline:
                if not script_done:
                    script_done = bool(page.evaluate(request.script))
                if script_done and len(got) == len(patterns):
                    break
                page.wait_for_timeout(250)
        finally:
            self._last = time.monotonic()
            page.close()
        missing = [name for name, _ in patterns if name not in got]
        if missing:
            what = "page script never succeeded" if not script_done else f"no payload for {missing}"
            raise CaptureTimeout(f"{request.url}: {what} within {timeout:.0f}s")
        return got


class HttpTransport:
    """Placeholder for a browser-free transport calling the JSON endpoints directly.

    Not implemented on purpose: those endpoints expect a token embedded in the site's JavaScript, and using
    it outside the site has not been approved. Implement this only after that decision is made."""

    def fetch(self, request: PageRequest) -> dict[str, Any]:
        raise NotImplementedError("HttpTransport is not implemented; use BrowserTransport (the default)")


_default: Transport | None = None


def get_transport() -> Transport:
    global _default
    if _default is None:
        _default = BrowserTransport()
    return _default


def set_transport(transport: Transport | None) -> None:
    """Replace the transport used by endpoints that are not given one explicitly."""
    global _default
    _default = transport
