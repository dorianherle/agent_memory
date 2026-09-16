import json
import threading
from http.client import HTTPConnection

from agent_memory.studio import Handler, SESSION, ThreadingHTTPServer, memory_view
from agent_memory.tests.helpers import MemoryTestCase


class StudioTests(MemoryTestCase):
    def setUp(self) -> None:
        super().setUp()
        SESSION.update(
            {
                "api_key": "",
                "project": "",
                "model": "composer-2.5",
                "agent": None,
                "run": None,
                "cancel": False,
            }
        )
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def _json(self, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {"Content-Type": "application/json"} if body else {}
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        raw = response.read()
        conn.close()
        data = json.loads(raw) if raw else {}
        return response.status, data

    def test_page_and_memory(self) -> None:
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/")
        response = conn.getresponse()
        html = response.read().decode()
        conn.close()
        self.assertEqual(response.status, 200)
        self.assertIn("One window", html)
        self.assertIn("Goal", html)

        status, data = self._json("GET", "/api/memory")
        self.assertEqual(status, 400)

        status, data = self._json(
            "POST", "/api/open", {"project": str(self.path), "api_key": "cursor_test"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["project"], str(self.path.resolve()))

        self.save("Login loads the whole users table every time")
        self.memory.save_user("Why is login so slow?")
        view = memory_view(str(self.path))
        self.assertIn("login", (view["goal"] or {}).get("text", "").lower())
        self.assertTrue(view["commits"])
        status, data = self._json("GET", "/api/memory")
        self.assertEqual(status, 200)
        self.assertIn("Goal", data["packet"])
        self.assertNotIn("Steering", data["packet"])
