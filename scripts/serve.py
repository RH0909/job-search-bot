"""Paikallinen palvelin työhakuraportille.

Ruksi raportissa tallentuu suoraan jobs.json:iin (status = "rejected").
Käyttö:  python scripts/serve.py [--port 8765] [--no-browser]
"""
import argparse
import json
import os
import tempfile
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from config import JOBS_FILE, REPORT_FILE
LOCK = threading.Lock()
PORT = 8765


def load_jobs():
    return json.loads(JOBS_FILE.read_text(encoding="utf-8-sig"))


def save_jobs(jobs):
    fd, tmp = tempfile.mkstemp(dir=JOBS_FILE.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)
    os.replace(tmp, JOBS_FILE)


def set_rejected(jobs, job_id, flag):
    for j in jobs:
        if j.get("id") == job_id:
            if flag and j.get("status") != "rejected":
                j["prev_status"] = j.get("status", "new")
                j["status"] = "rejected"
            elif not flag and j.get("status") == "rejected":
                j["status"] = j.pop("prev_status", None) or ("analyzed" if j.get("score") else "new")
            return True
    return False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _allowed_hosts(self):
        return {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}

    def _host_ok(self):
        # Suojaa DNS rebinding -hyökkäyksiltä
        return self.headers.get("Host", "") in self._allowed_hosts()

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"error": "host"})
        if self.path in ("/", "/report.html"):
            if not REPORT_FILE.exists():
                return self._send(404, b"report.html puuttuu - aja scout.py ensin", "text/plain; charset=utf-8")
            return self._send(200, REPORT_FILE.read_bytes(), "text/html; charset=utf-8")
        if self.path == "/api/rejected":
            with LOCK:
                ids = [j["id"] for j in load_jobs() if j.get("status") == "rejected"]
            return self._send(200, ids)
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._host_ok():
            return self._send(403, {"error": "host"})
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://{h}" for h in self._allowed_hosts()}:
            return self._send(403, {"error": "origin"})
        if self.path != "/api/reject":
            return self._send(404, {"error": "not found"})
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            return self._send(415, {"error": "content-type"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 10_000:
                raise ValueError("too large")
            payload = json.loads(self.rfile.read(length))
            job_id, flag = payload["id"], payload["rejected"]
            if not isinstance(job_id, str) or not isinstance(flag, bool):
                raise ValueError("bad types")
        except Exception:
            return self._send(400, {"error": "bad request"})
        with LOCK:
            jobs = load_jobs()
            if not set_rejected(jobs, job_id, flag):
                return self._send(404, {"error": "unknown id"})
            save_jobs(jobs)
            count = sum(1 for j in jobs if j.get("status") == "rejected")
        self._send(200, {"ok": True, "rejected": count})


def main():
    global PORT
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    PORT = args.port
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}/"
    print(f"Raportti: {url}  (Ctrl+C lopettaa)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nPysäytetty.")


if __name__ == "__main__":
    main()
