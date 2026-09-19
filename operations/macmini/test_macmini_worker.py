import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

import macmini_worker


class WorkerHealthTest(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), macmini_worker.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_health_does_not_load_workspace_catalog(self):
        with mock.patch.object(macmini_worker, "Controller", side_effect=KeyError("workspace")):
            with urllib.request.urlopen(self.base + "/healthz", timeout=2) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(json.load(response)["status"], "ok")

    def test_workspace_failure_returns_json_500(self):
        with mock.patch.object(macmini_worker, "Controller", side_effect=KeyError("workspace")):
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(self.base + "/v1/status", timeout=2)
            self.assertEqual(raised.exception.code, 500)
            self.assertEqual(json.load(raised.exception)["error"], "internal_error")


if __name__ == "__main__":
    unittest.main()
