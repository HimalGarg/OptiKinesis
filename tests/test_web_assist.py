import unittest

import requests

from web_assist import (
    FETCH_URL,
    SEARCH_URL,
    Article,
    TinyFishClient,
    WebAssistError,
    WindowInfo,
    capture_browser_url,
    clean_markdown,
    normalize_page_url,
)


class FakeResponse:
    def __init__(self, status_code=200, data=None, headers=None):
        self.status_code = status_code
        self._data = data if data is not None else {}
        self.headers = headers or {}

    def json(self):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


class FakeSession:
    """Records requests and returns queued responses (or raises queued errors)."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, headers=None, timeout=None, **kwargs):
        self.calls.append({"method": method, "url": url, "headers": headers,
                           "timeout": timeout, **kwargs})
        outcome = self.responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def search_payload(count=3):
    return {
        "query": "als exercise",
        "results": [
            {"position": i + 1, "site_name": f"site{i}.org", "title": f"  Title {i}  ",
             "snippet": "Some   useful\ntext", "url": f"https://site{i}.org/page"}
            for i in range(count)
        ],
        "total_results": count,
        "page": 0,
    }


class SearchTests(unittest.TestCase):
    def test_search_sends_key_and_query_and_parses_results(self):
        session = FakeSession(FakeResponse(200, search_payload(3)))
        answers = TinyFishClient("key-123", session=session).search("  als   exercise ")
        call = session.calls[0]
        self.assertEqual(call["method"], "GET")
        self.assertEqual(call["url"], SEARCH_URL)
        self.assertEqual(call["headers"]["X-API-Key"], "key-123")
        self.assertEqual(call["params"]["query"], "als exercise")
        self.assertEqual(len(answers.results), 3)
        first = answers.results[0]
        self.assertEqual(first.title, "Title 0")
        self.assertEqual(first.snippet, "Some useful text")
        self.assertEqual(first.site_name, "site0.org")

    def test_search_limits_and_skips_unusable_items(self):
        payload = search_payload(8)
        payload["results"].insert(0, {"title": "No url"})
        payload["results"].insert(0, {"title": "Bad scheme", "url": "javascript:alert(1)"})
        session = FakeSession(FakeResponse(200, payload))
        answers = TinyFishClient("k", session=session).search("q", limit=6)
        self.assertEqual(len(answers.results), 6)
        self.assertTrue(all(r.url.startswith("https://") for r in answers.results))

    def test_empty_query_is_rejected_without_a_request(self):
        session = FakeSession()
        with self.assertRaises(WebAssistError):
            TinyFishClient("k", session=session).search("   ")
        self.assertEqual(session.calls, [])

    def test_http_errors_become_friendly_messages(self):
        cases = {
            401: "rejected",
            402: "allowance",
            429: "wait about 30 seconds",
            503: "busy",
        }
        for status, phrase in cases.items():
            body = {"error": {"code": "X", "message": "m"}}
            session = FakeSession(FakeResponse(status, body, {"Retry-After": "30"}))
            with self.assertRaises(WebAssistError) as ctx:
                TinyFishClient("k", session=session).search("q")
            self.assertIn(phrase, ctx.exception.message)
            self.assertEqual(ctx.exception.status, status)

    def test_network_failure_is_reported_plainly(self):
        session = FakeSession(requests.ConnectionError("down"))
        with self.assertRaises(WebAssistError) as ctx:
            TinyFishClient("k", session=session).search("q")
        self.assertIn("internet", ctx.exception.message)

    def test_missing_key_is_explained(self):
        with self.assertRaises(WebAssistError) as ctx:
            TinyFishClient("")
        self.assertIn("TINYFISH_API_KEY", ctx.exception.message)


class FetchTests(unittest.TestCase):
    def test_fetch_posts_markdown_request_and_cleans_text(self):
        body = {
            "results": [{
                "url": "https://example.com/a",
                "final_url": "https://www.example.com/a",
                "title": "Living with ALS",
                "text": "# Living with ALS\n\n![logo](https://x/logo.png)\n\nRead [our guide](https://x/g) now.",
                "description": None, "language": "en", "author": None, "published_date": None,
                "latency_ms": 120, "format": "markdown",
            }],
            "errors": [],
        }
        session = FakeSession(FakeResponse(200, body))
        article = TinyFishClient("k", session=session).fetch("https://example.com/a#section")
        call = session.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["url"], FETCH_URL)
        self.assertEqual(call["json"]["urls"], ["https://example.com/a"])
        self.assertEqual(call["json"]["format"], "markdown")
        self.assertEqual(article.title, "Living with ALS")
        self.assertEqual(article.site_name, "example.com")
        self.assertNotIn("logo", article.text)
        self.assertIn("Read our guide now.", article.text)
        self.assertEqual(article.reading_minutes, 1)

    def test_ip_final_address_keeps_friendly_site_name(self):
        body = {"results": [{"url": "https://guide.example.edu/als", "final_url": "https://151.101.42.133/als",
                             "title": "Guide", "text": "Some text here."}], "errors": []}
        article = TinyFishClient("k", session=FakeSession(FakeResponse(200, body))).fetch(
            "https://guide.example.edu/als")
        self.assertEqual(article.site_name, "guide.example.edu")

    def test_per_url_errors_map_to_reasons(self):
        body = {"results": [], "errors": [{"url": "https://bank.example", "error": "login_required"}]}
        session = FakeSession(FakeResponse(200, body))
        with self.assertRaises(WebAssistError) as ctx:
            TinyFishClient("k", session=session).fetch("https://bank.example")
        self.assertIn("login", ctx.exception.message)
        self.assertEqual(ctx.exception.code, "login_required")

    def test_non_web_addresses_are_refused_before_any_request(self):
        session = FakeSession()
        client = TinyFishClient("k", session=session)
        for bad in ("file:///C:/secret.txt", "chrome://settings", "https://user:pw@site.com/x", ""):
            with self.assertRaises(WebAssistError):
                client.fetch(bad)
        self.assertEqual(session.calls, [])


class TextHelperTests(unittest.TestCase):
    def test_clean_markdown(self):
        source = (
            "Intro <span class='x'>text</span>\n\n\n\n"
            "[![badge](https://b/img.svg)](https://b)\n"
            "See <https://example.com> and [docs](https://d/(v2)).\n"
        )
        cleaned = clean_markdown(source)
        self.assertEqual(cleaned, "Intro text\n\nSee https://example.com and docs.")

    def test_normalize_page_url_drops_fragment(self):
        self.assertEqual(normalize_page_url(" https://a.org/p?q=1#top "), "https://a.org/p?q=1")

    def test_reading_time_rounds_to_minutes(self):
        article = Article(url="u", final_url="u", title="t", text="word " * 1000, site_name="s")
        self.assertEqual(article.reading_minutes, 5)


class FakeClipboard:
    def __init__(self, initial, browser_url):
        self.value = initial
        self.browser_url = browser_url

    def paste(self):
        return self.value

    def copy(self, text):
        self.value = text


class FakeKeys:
    def __init__(self, clipboard):
        self.clipboard = clipboard
        self.sent = []

    def hotkey(self, *keys, _pause=True):
        self.sent.append("+".join(keys))
        if keys == ("ctrl", "c") and self.clipboard.browser_url is not None:
            self.clipboard.value = self.clipboard.browser_url

    def press(self, key, _pause=True):
        self.sent.append(key)


OWN_PID = 4242
OVERLAY = WindowInfo(hwnd=1, pid=OWN_PID, name="python.exe")
BRAVE = WindowInfo(hwnd=2, pid=100, name="brave.exe")
WORD = WindowInfo(hwnd=3, pid=200, name="winword.exe")


class CaptureBrowserUrlTests(unittest.TestCase):
    def capture(self, front, clipboard, keys, last_external=None, wait_s=1.2):
        state = {"front": front, "activated": []}

        def activate(hwnd):
            state["activated"].append(hwnd)
            state["front"] = BRAVE if hwnd == BRAVE.hwnd else state["front"]
            return True

        url = capture_browser_url(
            foreground=lambda: state["front"], last_external=last_external, activate=activate,
            clipboard=clipboard, keys=keys, sleep=lambda s: None, wait_s=wait_s, own_pid=OWN_PID,
        )
        return url, state["activated"]

    def test_copies_address_and_restores_clipboard(self):
        clipboard = FakeClipboard("user's own text", "https://news.example/story#c")
        keys = FakeKeys(clipboard)
        url, activated = self.capture(BRAVE, clipboard, keys)
        self.assertEqual(url, "https://news.example/story")
        self.assertEqual(keys.sent, ["ctrl+l", "ctrl+c", "esc"])
        self.assertEqual(clipboard.value, "user's own text")
        self.assertEqual(activated, [])

    def test_switches_back_to_browser_when_optikinesis_is_in_front(self):
        clipboard = FakeClipboard("keep", "https://mail.example/inbox")
        keys = FakeKeys(clipboard)
        url, activated = self.capture(OVERLAY, clipboard, keys, last_external=BRAVE)
        self.assertEqual(activated, [BRAVE.hwnd])
        self.assertEqual(url, "https://mail.example/inbox")

    def test_refuses_when_no_browser_is_in_front(self):
        clipboard = FakeClipboard("keep", "https://x.example")
        keys = FakeKeys(clipboard)
        with self.assertRaises(WebAssistError) as ctx:
            self.capture(WORD, clipboard, keys, last_external=BRAVE)
        self.assertEqual(keys.sent, [])
        self.assertEqual(ctx.exception.code, "no_browser")

    def test_refuses_when_last_app_was_not_a_browser(self):
        clipboard = FakeClipboard("keep", "https://x.example")
        keys = FakeKeys(clipboard)
        with self.assertRaises(WebAssistError):
            self.capture(OVERLAY, clipboard, keys, last_external=WORD)
        self.assertEqual(keys.sent, [])

    def test_reports_failure_when_nothing_was_copied(self):
        clipboard = FakeClipboard("keep", None)
        keys = FakeKeys(clipboard)
        with self.assertRaises(WebAssistError):
            self.capture(BRAVE, clipboard, keys, wait_s=0.05)
        self.assertEqual(clipboard.value, "keep")


if __name__ == "__main__":
    unittest.main()
