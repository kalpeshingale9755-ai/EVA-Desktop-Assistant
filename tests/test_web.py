"""Stage 6a web tests. Run from EVA:  python -m unittest tests.test_web -v
No internet needed, and no browser is opened (a fake opener is used)."""
import unittest

from eva.capabilities.url_safety import UnsafeUrl, normalize_url
from eva.capabilities.web_actions import open_url, web_search
from eva.planner.local_web import parse_web_command


class UrlSafetyTests(unittest.TestCase):
    def test_good_addresses(self):
        self.assertEqual(normalize_url("google.com"), "https://google.com")
        self.assertEqual(normalize_url("https://www.youtube.com/watch?v=abc&t=5"),
                         "https://www.youtube.com/watch?v=abc&t=5")
        self.assertEqual(normalize_url("example.com:8080/path"), "https://example.com:8080/path")
        self.assertEqual(normalize_url("  HTTP://Example.COM  "), "http://example.com")

    def test_bad_addresses(self):
        bad = ["", "   ", "javascript:alert(1)", "file:///C:/Windows/win.ini", "data:text/html,hi",
               "mailto:a@b.com", "ftp://example.com", "http://127.0.0.1", "http://192.168.1.1/admin",
               "http://169.254.169.254/", "http://[::1]/", "localhost:3000", "http://localhost",
               "http://user:pass@evil.com", "https://evil.com@good.com", "https://xn--pple-43d.com",
               "http://printer.local", "http://intranet", "a b.com", "https://exa<mple.com",
               "https://example.com/\x07", "https://-bad-.com", "https://example.c", "x" * 2100]
        for b in bad:
            with self.assertRaises(UnsafeUrl, msg=repr(b)):
                normalize_url(b)


class WebActionTests(unittest.TestCase):
    def setUp(self):
        self.opened = []

    def opener_ok(self, url):
        self.opened.append(url)
        return True

    def test_open_url_ok(self):
        r = open_url("github.com", opener=self.opener_ok)
        self.assertEqual((r.outcome, self.opened), ("SUCCESS", ["https://github.com"]))
        self.assertIn("github.com", r.message)

    def test_unsafe_never_reaches_browser(self):
        for bad in ["javascript:alert(1)", "http://127.0.0.1", "file:///C:/x"]:
            r = open_url(bad, opener=self.opener_ok)
            self.assertEqual(r.outcome, "FAILED")
        self.assertEqual(self.opened, [])

    def test_browser_failure(self):
        r = open_url("github.com", opener=lambda u: False)
        self.assertEqual(r.outcome, "FAILED")

    def test_search_builds_safe_url(self):
        r = web_search("python & java tutorials", engine="google", opener=self.opener_ok)
        self.assertEqual(r.outcome, "SUCCESS")
        self.assertEqual(self.opened, ["https://www.google.com/search?q=python+%26+java+tutorials"])

    def test_search_rejects_bad_input(self):
        for bad in ["", "   ", "a\x00b", "x" * 300, None, 5]:
            self.assertEqual(web_search(bad, opener=self.opener_ok).outcome, "FAILED", msg=repr(bad))
        self.assertEqual(self.opened, [])

    def test_unknown_engine_falls_back_to_google(self):
        web_search("cats", engine="nope", opener=self.opener_ok)
        self.assertTrue(self.opened[0].startswith("https://www.google.com/search?q="))


class LocalWebParserTests(unittest.TestCase):
    def test_searches(self):
        m = parse_web_command("Search for python tutorials")
        self.assertEqual((m.capability, m.args), ("web_search", {"query": "python tutorials"}))
        self.assertEqual(parse_web_command("google best laptops under 50000").args["query"],
                         "best laptops under 50000")
        self.assertEqual(parse_web_command("please look up weather in bhopal on google").args["query"],
                         "weather in bhopal")

    def test_youtube_search(self):
        m = parse_web_command("search for lo-fi music on youtube")
        self.assertEqual(m.capability, "open_url")
        self.assertIn("youtube.com/results?search_query=lo-fi+music", m.args["url"])

    def test_open_sites(self):
        self.assertEqual(parse_web_command("open youtube").args["url"], "https://www.youtube.com")
        self.assertEqual(parse_web_command("go to github dot com").args["url"], "github.com")
        self.assertEqual(parse_web_command("Visit the website stackoverflow").args["url"],
                         "https://stackoverflow.com")
        self.assertEqual(parse_web_command("open google maps").args["url"], "https://maps.google.com")

    def test_not_web(self):
        for t in ["open notepad", "open notes.txt", "open report.pdf", "open app.py", "close chrome",
                  "what time is it", "search", "open", "go to sleep", "open the file resume.docx",
                  "search for python and open youtube", "open youtube then search for cats"]:
            self.assertIsNone(parse_web_command(t), msg=t)


if __name__ == "__main__":
    unittest.main()
