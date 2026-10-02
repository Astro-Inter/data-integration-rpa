import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class RunnerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        root.joinpath("job_server.py").write_text(Path(__file__).with_name("job_server.py").read_text())
        cls.marker = root / "write-marker"
        root.joinpath("jobs.json").write_text(json.dumps({
            "write": {"command": [sys.executable, "-c", f"from pathlib import Path; Path({str(cls.marker)!r}).write_text('executed')"], "timeout_seconds": 10, "required_env": []},
            "fail": {"command": [sys.executable, "-c", "raise SystemExit(9)"], "timeout_seconds": 10, "required_env": []},
            "slow": {"command": [sys.executable, "-c", "import time; time.sleep(0.5)"], "timeout_seconds": 10, "required_env": []},
            "secret": {"command": [sys.executable, "-c", "raise SystemExit(0)"], "timeout_seconds": 10, "required_env": ["ASTRO_TEST_UNCONFIGURED_SECRET"]},
            "simulate": {"command": [sys.executable, "-c", "import os; raise SystemExit(0 if os.getenv('TEST_DRY_RUN') == 'true' else 7)"], "timeout_seconds": 10, "required_env": [], "dry_run_env": "TEST_DRY_RUN"},
            "timeout": {"command": [sys.executable, "-c", "import time; time.sleep(10)"], "timeout_seconds": 0.1, "required_env": []},
        }))
        spec = importlib.util.spec_from_file_location("test_runner", root / "job_server.py")
        cls.runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.runner)
        cls.server = cls.runner.ThreadingHTTPServer(("127.0.0.1", 0), cls.runner.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.temp.cleanup()

    def setUp(self):
        with self.runner.LOCK:
            self.runner.RUNS.clear()

    def request(self, path, body=None):
        request = Request(self.url + path, data=json.dumps(body).encode() if body is not None else None, headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            with error:
                return error.code, json.load(error)

    def wait(self, run_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            _, data = self.request("/runs/" + run_id)
            if data["status"] != "running":
                return data
            time.sleep(0.02)
        self.fail("Job did not finish")

    def test_dry_run_does_not_write(self):
        self.request("/run", {"job": "write", "run_id": "dry", "dry_run": True})
        self.assertEqual(self.wait("dry")["status"], "validated")
        self.assertFalse(self.marker.exists())

    def test_real_failure_is_not_success(self):
        self.request("/run", {"job": "fail", "run_id": "failure", "dry_run": False})
        data = self.wait("failure")
        self.assertEqual((data["status"], data["exit_code"]), ("failed", 9))

    def test_subprocess_receives_dry_run_flag(self):
        self.request("/run", {"job": "simulate", "run_id": "simulated", "dry_run": True})
        self.assertEqual(self.wait("simulated")["status"], "success")

    def test_overlap_rejected_and_same_id_not_reexecuted(self):
        body = {"job": "slow", "run_id": "same", "dry_run": False}
        self.assertEqual(self.request("/run", body)[0], 202)
        self.assertEqual(self.request("/run", body)[0], 200)
        self.assertEqual(self.request("/run", {**body, "run_id": "other"})[0], 409)
        self.wait("same")

    def test_commands_cannot_be_injected(self):
        self.assertEqual(self.request("/run", {"job": "arbitrary command", "run_id": "bad", "dry_run": False})[0], 400)
        self.assertEqual(self.request("/run", {"job": "fail", "run_id": "bad", "dry_run": "false"})[0], 400)

    def test_missing_configuration_fails_without_secret_values(self):
        code, data = self.request("/run", {"job": "secret", "run_id": "secret", "dry_run": True})
        self.assertEqual(code, 503)
        self.assertEqual(data["fields"], ["ASTRO_TEST_UNCONFIGURED_SECRET"])

    def test_missing_run_is_interrupted(self):
        self.assertEqual(self.request("/runs/lost"), (404, {"status": "interrupted"}))

    @unittest.skipUnless(os.name == "posix", "Linux process group test")
    def test_timeout_terminates_subprocess(self):
        self.request("/run", {"job": "timeout", "run_id": "timed", "dry_run": False})
        data = self.wait("timed")
        self.assertEqual((data["status"], data["exit_code"]), ("timeout", 124))


if __name__ == "__main__":
    unittest.main()

