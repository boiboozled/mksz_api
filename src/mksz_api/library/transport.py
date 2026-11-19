"""How endpoints get their JSON: a Transport turns a PageRequest into named payloads.

mksz.hu and livescore.mksz.hu are Vue apps that load all data as JSON after the page opens. The default
BrowserTransport opens the page in headless Chromium like a normal visitor and keeps the JSON responses
the page receives that match each Capture's pattern. Endpoints never build API calls themselves, so a
different transport (e.g. plain HTTP) can be plugged in without touching them.
"""

from __future__ import annotations

import atexit
import queue
import re
import threading
import time
from concurrent.futures import Future
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


class _BrowserThread:
    """A daemon thread that runs every Playwright call of one BrowserTransport.

    Playwright's sync API refuses to run in a thread with a running asyncio loop (Jupyter, IPython,
    async apps), and its objects may only be used from the thread that created them. Running all browser
    work on this dedicated thread makes the transport usable from anywhere, including notebooks."""

    def __init__(self):
        self._jobs: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._loop, name="mksz-browser", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                return
            fn, args, fut = job
            if not fut.set_running_or_notify_cancel():
                continue
            try:
                fut.set_result(fn(*args))
            except BaseException as e:  # noqa: BLE001 - handed back to the caller
                fut.set_exception(e)

    def run(self, fn, *args):
        if threading.current_thread() is self._thread:
            return fn(*args)
        fut: Future = Future()
        self._jobs.put((fn, args, fut))
        return fut.result()

    def stop(self) -> None:
        self._jobs.put(None)
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=30)


class BrowserTransport:
    """Headless Chromium via Playwright. One browser is shared by all requests; pages are loaded at most
    one per `delay` seconds to stay polite. Works in scripts and in Jupyter notebooks alike: the browser
    lives on its own worker thread."""

    def __init__(self, delay: float = 2.0, timeout: float = 45.0, headless: bool = True, retries: int = 1):
        self.delay = delay
        self.timeout = timeout
        self.headless = headless
        self.retries = retries
        self._pw = None
        self._browser = None
        self._context = None
        self._last = 0.0
        self._worker: _BrowserThread | None = None
        self._lock = threading.Lock()
        self._atexit = False

    def _run(self, fn, *args):
        with self._lock:
            if self._worker is None:
                self._worker = _BrowserThread()
                if not self._atexit:
                    atexit.register(self.close)
                    self._atexit = True
            worker = self._worker
        return worker.run(fn, *args)

    def _ensure(self):  # runs on the worker thread
        if self._context is None:
            from playwright.sync_api import sync_playwright

            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=self.headless)
            self._context = self._browser.new_context(locale="hu-HU")
        return self._context

    def _close_browser(self) -> None:  # runs on the worker thread
        for obj, meth in ((self._context, "close"), (self._browser, "close"), (self._pw, "stop")):
            if obj is not None:
                try:
                    getattr(obj, meth)()
                except Exception:  # noqa: BLE001 - best effort at shutdown
                    pass
        self._pw = self._browser = self._context = None

    def close(self) -> None:
        """Close the browser and stop its thread. The transport can still be used afterwards: the next
        fetch starts a new browser."""
        with self._lock:
            worker, self._worker = self._worker, None
        if worker is None:
            return
        try:
            worker.run(self._close_browser)
        finally:
            worker.stop()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def fetch(self, request: PageRequest) -> dict[str, Any]:
        return self._run(self._fetch_with_retries, request)

    def _fetch_with_retries(self, request: PageRequest) -> dict[str, Any]:  # runs on the worker thread
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
