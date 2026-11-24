import ssl
import unittest
from urllib.error import HTTPError
from urllib.request import HTTPSHandler, ProxyHandler, build_opener, urlopen

from harness.platform_capabilities import sandbox_exec_available
from harness.web_fetch.frozen_inputs import fixture_server, verify_loopback_sandbox


class FrozenFixtureTests(unittest.TestCase):
    def test_default_fixture_keeps_the_frozen_profile_route(self):
        record = {"track_id": "case-one", "html": "<h1>Frozen case</h1>",
                  "url": "https://source.example.test/article"}
        with fixture_server([record]) as (base, ca_cert):
            context = ssl.create_default_context(cafile=str(ca_cert))
            with urlopen(base + "/case/case-one?nocdn=true", context=context, timeout=5) as response:
                self.assertEqual(response.read().decode(), record["html"])

    def test_inline_query_navigation_keeps_the_same_frozen_page(self):
        frozen = "<h1>Frozen case</h1>"
        record = {"track_id": "case-one", "html": frozen,
                  "url": "https://source.example.test/article"}
        with fixture_server([record], preserve_original_origin=True) as (proxy, ca_cert):
            context = ssl.create_default_context(cafile=str(ca_cert))
            opener = build_opener(ProxyHandler({"https": proxy}), HTTPSHandler(context=context))
            with opener.open(record["url"] + "?nocdn=true", timeout=5) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read().decode(), frozen)

    def test_same_origin_path_uses_exact_frozen_query_without_crossing_cases(self):
        records = [
            {"track_id": "first", "html": "<h1>First</h1>",
             "url": "https://source.example.test/search?q=first"},
            {"track_id": "second", "html": "<h1>Second</h1>",
             "url": "https://source.example.test/search?q=second"},
        ]
        with fixture_server(records, preserve_original_origin=True) as (proxy, ca_cert):
            context = ssl.create_default_context(cafile=str(ca_cert))
            opener = build_opener(ProxyHandler({"https": proxy}), HTTPSHandler(context=context))
            with opener.open(records[0]["url"], timeout=5) as response:
                self.assertEqual(response.read().decode(), records[0]["html"])
            with opener.open(records[1]["url"], timeout=5) as response:
                self.assertEqual(response.read().decode(), records[1]["html"])
            with self.assertRaises(HTTPError) as error:
                opener.open("https://source.example.test/search?q=unknown", timeout=5)
            self.assertEqual(error.exception.code, 404)

    def test_scheme_and_port_keep_distinct_origins(self):
        records = [
            {"track_id": "default", "html": "<h1>Default</h1>", "url": "https://source.example.test/page"},
            {"track_id": "port", "html": "<h1>Port</h1>", "url": "https://source.example.test:8443/page"},
            {"track_id": "plain", "html": "<h1>Plain</h1>", "url": "http://source.example.test/page"},
        ]
        with fixture_server(records, preserve_original_origin=True) as (proxy, ca_cert):
            context = ssl.create_default_context(cafile=str(ca_cert))
            opener = build_opener(ProxyHandler({"https": proxy, "http": proxy}), HTTPSHandler(context=context))
            for record in records:
                with opener.open(record["url"], timeout=5) as response:
                    self.assertEqual(response.read().decode(), record["html"])
            with self.assertRaises(OSError):
                opener.open("https://source.example.test:9443/page", timeout=5)

    def test_isolation_probe_requests_the_exact_query_and_port(self):
        records = [
            {"track_id": "first", "html": "<h1>First</h1>",
             "url": "https://source.example.test:8443/search?q=first"},
            {"track_id": "second", "html": "<h1>Second</h1>",
             "url": "https://source.example.test:8443/search?q=second"},
        ]
        with fixture_server(records, preserve_original_origin=True) as (proxy, ca_cert):
            if not sandbox_exec_available():
                self.skipTest("OS sandbox unavailable")
            result = verify_loopback_sandbox(proxy, ca_cert, records[1], True)
        self.assertIn("loopback_body_sha256", result)


if __name__ == "__main__":
    unittest.main()
