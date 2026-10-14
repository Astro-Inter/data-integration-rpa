"""Internal container runner. Commands are fixed in jobs.json, never supplied by HTTP."""
import json
import os
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

JOBS = json.loads(Path(__file__).with_name("jobs.json").read_text())
LOCK = threading.Lock()
RUNS = {}


def execute(run_id, job, dry_run):
    config = JOBS[job]
    env = dict(os.environ)
    if config.get("dry_run_env"):
        env[config["dry_run_env"]] = "true" if dry_run else "false"
    try:
        if dry_run and not config.get("dry_run_env"):
            # A configuration check cannot claim that a real run succeeded.
            result = {"status": "validated", "exit_code": None}
        else:
            process = subprocess.Popen(config["command"], env=env, start_new_session=True)
            try:
                code = process.wait(timeout=config["timeout_seconds"])
                result = {"status": "success" if code == 0 else "failed", "exit_code": code}
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                result = {"status": "timeout", "exit_code": 124}
    except Exception:
        result = {"status": "failed", "exit_code": 1}
    with LOCK:
        RUNS[run_id].update(result, finished_at=time.time())
    print(json.dumps({"event": "job_finished", "run_id": run_id, "job": job, **result}), flush=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Never log request headers, URLs or secrets.

    def respond(self, code, data):
        content = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        if self.path == "/health":
            return self.respond(200, {"status": "ok"})
        if self.path.startswith("/runs/"):
            with LOCK:
                run = RUNS.get(self.path.removeprefix("/runs/"))
                return self.respond(200 if run else 404, run or {"status": "interrupted"})
        self.respond(404, {"error": "not_found"})

    def do_POST(self):
        if self.path != "/run":
            return self.respond(404, {"error": "not_found"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096:
                return self.respond(400, {"error": "invalid_body"})
            body = json.loads(self.rfile.read(length))
            job, run_id, dry_run = body["job"], body["run_id"], body["dry_run"]
            if job not in JOBS or not isinstance(run_id, str) or not 1 <= len(run_id) <= 128 or not isinstance(dry_run, bool):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            return self.respond(400, {"error": "invalid_job"})
        missing = [key for key in JOBS[job]["required_env"] if not os.getenv(key)]
        if missing:
            return self.respond(503, {"error": "missing_configuration", "fields": missing})
        with LOCK:
            if run_id in RUNS:
                return self.respond(200, RUNS[run_id])
            if any(run["status"] == "running" for run in RUNS.values()):
                return self.respond(409, {"error": "job_already_running"})
            # Bound the in-memory history. Durable Objects persist the latest run.
            while len(RUNS) >= 100:
                RUNS.pop(next(iter(RUNS)))
            RUNS[run_id] = {"run_id": run_id, "job": job, "dry_run": dry_run, "status": "running", "started_at": time.time()}
            thread = threading.Thread(target=execute, args=(run_id, job, dry_run), daemon=True)
            thread.start()
            return self.respond(202, RUNS[run_id])


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
