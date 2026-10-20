"""TinyFish-powered web helpers for OptiKinesis: answer cards and the clean reader.

* ``TinyFishClient.search`` calls the TinyFish Search API and returns a few
  results to show as large cards.
* ``TinyFishClient.fetch`` calls the TinyFish Fetch API and returns a page as
  cleaned markdown for the large-text reader.
* ``capture_browser_url`` copies the address of the page open in the user's
  browser, so "Read this page" works without typing a URL.

This module talks to the REST API with ``requests`` rather than the official
``tinyfish`` SDK, because the SDK needs Python 3.11+ while this project is
pinned to Python 3.10 by MediaPipe.  It has no Qt dependency so it can be
unit tested directly.

API contract (https://docs.tinyfish.ai/search-api/reference and
https://docs.tinyfish.ai/fetch-api/reference): both endpoints take an
``X-API-Key`` header; errors come back as ``{"error": {"code", "message"}}``.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger("optikinesis.web")

SEARCH_URL = "https://api.search.tinyfish.ai"
FETCH_URL = "https://api.fetch.tinyfish.ai"

SEARCH_TIMEOUT_S = 15.0       # docs: typical latency 1-3 s, suggested timeout 10 s
FETCH_TIMEOUT_S = 150.0       # docs: recommend a client timeout of at least 150 s
FETCH_PER_URL_TIMEOUT_MS = 45000
MAX_ANSWERS = 6
MAX_SNIPPET_CHARS = 320
MAX_READER_CHARS = 150_000
WORDS_PER_MINUTE = 200

SEARCH_PURPOSE = (
    "Answer a question for a person with a motor disability who reads results "
    "as a few large cards; prefer clear, reputable, readable pages."
)
FETCH_PURPOSE = "Show the main article text in a large-print reader for accessibility."

MISSING_KEY_MESSAGE = (
    "Web answers and the reader need a TinyFish API key. "
    "Add TINYFISH_API_KEY to the .env file and restart."
)

# Friendly text for TinyFish Fetch per-URL error codes.
FETCH_ERROR_MESSAGES = {
    "login_required": "This page needs a login, so it can't be shown in the reader.",
    "bot_blocked": "This website blocked the reader. Try opening it in the browser instead.",
    "page_not_found": "That page no longer exists.",
    "timeout": "The page took too long to load. Please try again.",
    "empty_content": "This page has no readable text.",
    "content_too_large": "This page is too large to show in the reader.",
    "target_unreachable": "That website couldn't be reached.",
    "invalid_url": "That page address isn't valid.",
}

# Foreground processes treated as web browsers by capture_browser_url().
BROWSER_PROCESSES = frozenset({
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe",
    "opera.exe", "opera_gx.exe", "vivaldi.exe", "arc.exe",
})


class WebAssistError(Exception):
    """A failure with a message that is safe and clear to show the user."""

    def __init__(self, message: str, code: Optional[str] = None, status: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    site_name: str
    date: str = ""


@dataclass(frozen=True)
class SearchResults:
    query: str
    results: Sequence[SearchResult]


@dataclass(frozen=True)
class Article:
    url: str
    final_url: str
    title: str
    text: str            # cleaned markdown
    site_name: str
    description: str = ""
    author: str = ""
    published_date: str = ""

    @property
    def word_count(self) -> int:
        return len(re.findall(r"\w+", self.text))

    @property
    def reading_minutes(self) -> int:
        return max(1, round(self.word_count / WORDS_PER_MINUTE))


# ── text helpers ──────────────────────────────────────────────────────────

_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_EMPTY_LINK = re.compile(r"\[\s*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\((?:[^()\s]|\([^)]*\))*(?:\s+\"[^\"]*\")?\)")
_AUTOLINK = re.compile(r"<(https?://[^>\s]+)>")
_HTML_TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*)?/?>")
_BLANK_RUN = re.compile(r"\n{3,}")


def clean_markdown(text: str) -> str:
    """Reduce fetched markdown to plain readable text for the reader.

    Images are removed (they can't load in the reader), links keep only their
    words (reader text is not clickable), and stray HTML tags are dropped.
    """
    if not text:
        return ""
    cleaned = _IMAGE.sub("", text)
    cleaned = _EMPTY_LINK.sub("", cleaned)
    cleaned = _LINK.sub(r"\1", cleaned)
    cleaned = _AUTOLINK.sub(r"\1", cleaned)
    cleaned = _HTML_TAG.sub("", cleaned)
    cleaned = "\n".join(line.rstrip() for line in cleaned.splitlines())
    cleaned = _BLANK_RUN.sub("\n\n", cleaned).strip()
    if len(cleaned) > MAX_READER_CHARS:
        cut = cleaned.rfind("\n", 0, MAX_READER_CHARS)
        cleaned = cleaned[: cut if cut > 0 else MAX_READER_CHARS].rstrip()
        cleaned += "\n\n*(Article shortened for the reader.)*"
    return cleaned


def site_name_of(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return host[4:] if host.startswith("www.") else host


def _is_ip_address(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def normalize_page_url(url: str) -> str:
    """Validate a page address for the reader and drop its #fragment.

    Raises WebAssistError for anything that isn't a plain http(s) address.
    """
    candidate = (url or "").strip()
    parts = urlsplit(candidate)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise WebAssistError("That isn't a web page address the reader can open.", code="invalid_url")
    if parts.username or parts.password:
        raise WebAssistError("Addresses containing a login can't be opened in the reader.", code="invalid_url")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


# ── TinyFish REST client ──────────────────────────────────────────────────

class TinyFishClient:
    """Minimal client for TinyFish Search and Fetch (free tiers)."""

    def __init__(self, api_key: str, session=None):
        if not api_key:
            raise WebAssistError(MISSING_KEY_MESSAGE, code="MISSING_API_KEY")
        self._api_key = api_key
        if session is None:
            import requests

            session = requests.Session()
        self._session = session

    @classmethod
    def from_env(cls) -> Optional["TinyFishClient"]:
        key = (os.getenv("TINYFISH_API_KEY") or "").strip()
        return cls(key) if key else None

    def _headers(self) -> dict:
        return {"X-API-Key": self._api_key, "Accept": "application/json"}

    def _request(self, method: str, url: str, timeout: float, **kwargs):
        import requests

        try:
            response = self._session.request(method, url, headers=self._headers(), timeout=timeout, **kwargs)
        except requests.Timeout as exc:
            raise WebAssistError("The web service took too long to answer. Please try again.",
                                 code="timeout") from exc
        except requests.RequestException as exc:
            raise WebAssistError("Couldn't reach the internet. Check the connection and try again.",
                                 code="network") from exc

        if response.status_code >= 400:
            raise _http_error(response)
        try:
            return response.json()
        except ValueError as exc:
            raise WebAssistError("The web service sent an unexpected reply.", code="bad_response",
                                 status=response.status_code) from exc

    def search(self, query: str, limit: int = MAX_ANSWERS) -> SearchResults:
        query = _collapse(query)
        if not query:
            raise WebAssistError("Please say or type what you want to find.", code="empty_query")
        data = self._request(
            "GET", SEARCH_URL, SEARCH_TIMEOUT_S,
            params={"query": query, "purpose": SEARCH_PURPOSE},
        )
        results: List[SearchResult] = []
        for item in data.get("results") or []:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            title = _collapse(str(item.get("title") or ""))
            if not url.startswith(("http://", "https://")) or not title:
                continue
            snippet = _collapse(str(item.get("snippet") or ""))
            if len(snippet) > MAX_SNIPPET_CHARS:
                snippet = snippet[: MAX_SNIPPET_CHARS - 1].rstrip() + "…"
            results.append(SearchResult(
                title=title,
                url=url,
                snippet=snippet,
                site_name=_collapse(str(item.get("site_name") or "")) or site_name_of(url),
                date=_collapse(str(item.get("date") or "")),
            ))
            if len(results) >= limit:
                break
        return SearchResults(query=query, results=tuple(results))

    def fetch(self, url: str) -> Article:
        page_url = normalize_page_url(url)
        data = self._request(
            "POST", FETCH_URL, FETCH_TIMEOUT_S,
            json={
                "urls": [page_url],
                "format": "markdown",
                "purpose": FETCH_PURPOSE,
                "per_url_timeout_ms": FETCH_PER_URL_TIMEOUT_MS,
            },
        )
        results = data.get("results") or []
        if not results:
            errors = data.get("errors") or []
            code = str(errors[0].get("error") or "") if errors and isinstance(errors[0], dict) else ""
            raise WebAssistError(
                FETCH_ERROR_MESSAGES.get(code, "This page couldn't be loaded for reading."),
                code=code or "fetch_failed",
            )
        item = results[0]
        text = clean_markdown(item.get("text") if isinstance(item.get("text"), str) else "")
        if not text:
            raise WebAssistError(FETCH_ERROR_MESSAGES["empty_content"], code="empty_content")
        final_url = str(item.get("final_url") or page_url)
        # Some fetches report a raw IP as the final address; keep the friendly name.
        site = site_name_of(final_url)
        if not site or _is_ip_address(site):
            site = site_name_of(page_url)
        return Article(
            url=page_url,
            final_url=final_url,
            title=_collapse(str(item.get("title") or "")) or site,
            text=text,
            site_name=site,
            description=_collapse(str(item.get("description") or "")),
            author=_collapse(str(item.get("author") or "")),
            published_date=_collapse(str(item.get("published_date") or "")),
        )


def _http_error(response) -> WebAssistError:
    status = response.status_code
    code = ""
    try:
        body = response.json()
        code = str((body.get("error") or {}).get("code") or "")
    except (ValueError, AttributeError):
        pass
    logger.warning("TinyFish request failed: HTTP %s %s", status, code)
    if status == 401:
        message = "The TinyFish API key was rejected. Check TINYFISH_API_KEY in the .env file."
    elif status == 402:
        message = "Today's free TinyFish allowance is used up. Please try again tomorrow."
    elif status == 429:
        retry = response.headers.get("Retry-After", "") if hasattr(response, "headers") else ""
        wait = f" about {retry} seconds" if str(retry).isdigit() else " a minute"
        message = f"Too many requests right now. Please wait{wait} and try again."
    elif status in (500, 502, 503, 504):
        message = "The web service is busy. Please try again shortly."
    else:
        message = "The web request didn't work. Please try again."
    return WebAssistError(message, code=code or f"http_{status}", status=status)


# ── "Read this page": copy the address of the page in the user's browser ──

@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    pid: int
    name: str  # lower-case executable name, "" if unknown


def _user32():
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return None
    if not hasattr(ctypes, "windll"):
        return None
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    return user32


def _process_name(pid: int) -> str:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return os.path.basename(buffer.value).lower()
    finally:
        kernel32.CloseHandle(handle)


def foreground_window() -> Optional[WindowInfo]:
    """The foreground window, its process id and executable name (Windows only)."""
    user32 = _user32()
    if user32 is None:
        return None
    import ctypes
    from ctypes import wintypes

    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return WindowInfo(hwnd=int(hwnd), pid=int(pid.value), name=_process_name(pid.value))


def foreground_process_name() -> Optional[str]:
    """Executable name of the foreground window's process (Windows only)."""
    info = foreground_window()
    return info.name if info else None


def activate_window(hwnd: int) -> bool:
    """Bring a window to the front. Windows allows this while our app is in front."""
    user32 = _user32()
    if user32 is None:
        return False
    return bool(user32.SetForegroundWindow(hwnd))


def capture_browser_url(
    foreground: Callable[[], Optional[WindowInfo]] = foreground_window,
    last_external: Optional[WindowInfo] = None,
    activate: Callable[[int], bool] = activate_window,
    clipboard=None,
    keys=None,
    sleep: Callable[[float], None] = time.sleep,
    wait_s: float = 1.2,
    own_pid: Optional[int] = None,
) -> str:
    """Copy the address of the page open in the user's browser.

    Sends Ctrl+L, Ctrl+C, Esc to the browser and reads the clipboard, then
    restores the previous clipboard text.  Only acts on a known browser.

    If OptiKinesis itself is in front (for example right after its window
    was clicked), ``last_external`` (the last app the user used outside
    OptiKinesis) is brought back to the front first, as long as it is a
    browser.  The injected keystrokes are ignored by the caregiver hotkey
    listener.
    """
    own_pid = os.getpid() if own_pid is None else own_pid
    current = foreground()
    if (
        (current is None or current.pid == own_pid)
        and last_external is not None
        and last_external.name in BROWSER_PROCESSES
    ):
        activate(last_external.hwnd)
        sleep(0.25)
        current = foreground()
    if current is None or current.name not in BROWSER_PROCESSES:
        raise WebAssistError(
            "Open the page in Chrome, Edge, Firefox or Brave first, then choose Read this page.",
            code="no_browser",
        )
    if clipboard is None:
        import pyperclip as clipboard  # noqa: N813 - module used as an object
    if keys is None:
        import pyautogui as keys

    try:
        previous = clipboard.paste()
    except Exception:
        previous = None
    sentinel = f"optikinesis-{uuid.uuid4().hex}"
    clipboard.copy(sentinel)
    copied = ""
    try:
        keys.hotkey("ctrl", "l", _pause=False)
        sleep(0.15)
        keys.hotkey("ctrl", "c", _pause=False)
        deadline = time.monotonic() + wait_s
        while True:
            sleep(0.05)
            value = clipboard.paste()
            if value and value != sentinel:
                copied = value.strip()
                break
            if time.monotonic() >= deadline:
                break
        keys.press("esc", _pause=False)
    except Exception as exc:
        raise WebAssistError("Couldn't read the browser's address. Please try again.",
                             code="capture_failed") from exc
    finally:
        try:
            clipboard.copy(previous if isinstance(previous, str) else "")
        except Exception:
            logger.debug("Could not restore the clipboard")

    if not copied:
        raise WebAssistError("Couldn't read the browser's address. Click inside the page and try again.",
                             code="capture_failed")
    return normalize_page_url(copied)
