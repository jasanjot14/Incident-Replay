import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

from server import start_servers


class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gateway, cls.downstream, _ = start_servers(0, 0)
        cls.thread = threading.Thread(target=cls.gateway.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.gateway.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.gateway.shutdown()
        cls.downstream.shutdown()
        cls.gateway.server_close()
        cls.downstream.server_close()

    def run_scenario(self, fault):
        try:
            with urlopen(self.base + "/api/run?fault=" + fault) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.load(exc)

    def test_healthy_trace_spans_two_services(self):
        code, data = self.run_scenario("healthy")
        self.assertEqual(code, 200)
        spans = data["trace"]["spans"]
        self.assertEqual({s["service"] for s in spans}, {"api-gateway", "recommendation-service", "sqlite"})
        self.assertEqual(len(spans), 4)

    def test_backend_error_and_silent_empty_result_are_distinct(self):
        code, data = self.run_scenario("database")
        self.assertEqual(code, 502)
        self.assertTrue(any(s["service"] == "sqlite" and s["status"] == "error" for s in data["trace"]["spans"]))
        code, data = self.run_scenario("empty")
        self.assertEqual(code, 200)
        self.assertEqual(data["result"]["recommendations"], [])
        self.assertTrue(any(s["status"] == "warning" for s in data["trace"]["spans"]))

    def test_trace_can_be_retrieved_by_id(self):
        _, data = self.run_scenario("slow")
        self.assertTrue(any(s["service"] == "sqlite" and s["status"] == "warning"
                            for s in data["trace"]["spans"]))
        self.assertTrue(any(s["service"] == "api-gateway" and s["status"] == "warning"
                            for s in data["trace"]["spans"]))
        with urlopen(self.base + "/api/traces/" + data["trace_id"]) as response:
            saved = json.load(response)
        self.assertEqual(saved["id"], data["trace_id"])


if __name__ == "__main__":
    unittest.main()
