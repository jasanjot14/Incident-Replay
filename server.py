"""Two-service incident lab with trace propagation and a browser timeline.

No dependencies beyond Python's standard library. The span model is deliberately
small; production interoperability with OpenTelemetry is a future extension.
"""
import argparse
import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).parent / "dist"
SCENARIOS = {"healthy", "slow", "database", "empty"}


class TraceStore:
    def __init__(self):
        self.lock = threading.Lock()
        self.traces = {}

    @contextmanager
    def span(self, trace_id, name, service, parent=None):
        item = {"id": uuid.uuid4().hex[:12], "parent": parent, "name": name,
                "service": service, "start": time.time(), "end": None,
                "status": "ok", "detail": ""}
        with self.lock:
            self.traces.setdefault(trace_id, []).append(item)
        try:
            yield item
        except Exception as exc:
            item["status"] = "error"
            item["detail"] = str(exc)
            raise
        finally:
            item["end"] = time.time()

    def get(self, trace_id):
        with self.lock:
            spans = [dict(s) for s in self.traces.get(trace_id, [])]
        return {"id": trace_id, "spans": sorted(spans, key=lambda s: s["start"])}

    def recent(self):
        with self.lock:
            ids = list(self.traces)[-25:]
        return [self.get(i) for i in reversed(ids)]


class JSONHandler(BaseHTTPRequestHandler):
    def respond(self, status, value):
        raw = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(raw)
        except BrokenPipeError:
            pass

    def log_message(self, *_args):
        pass


def make_downstream(store):
    database = sqlite3.connect(":memory:", check_same_thread=False)
    database.execute("CREATE TABLE recommendations (title TEXT NOT NULL)")
    database.executemany("INSERT INTO recommendations (title) VALUES (?)", [
        ("The Station",), ("Signal Lost",), ("Paper City",)
    ])
    database.commit()
    database_lock = threading.Lock()

    class Downstream(JSONHandler):
        def do_GET(self):
            params = parse_qs(urlparse(self.path).query)
            fault = params.get("fault", ["healthy"])[0]
            trace_id = self.headers.get("X-Trace-ID", uuid.uuid4().hex)
            parent = self.headers.get("X-Parent-Span")
            with store.span(trace_id, "GET /recommendations", "recommendation-service", parent) as span:
                try:
                    with store.span(trace_id, "query recommendations", "sqlite", span["id"]) as db:
                        if fault == "database":
                            raise sqlite3.OperationalError("database unavailable (injected)")
                        if fault == "slow":
                            time.sleep(.25)  # Inject latency within the database span.
                            db["status"] = "warning"
                            db["detail"] = "250 ms delay injected before SQLite query"
                        with database_lock:
                            # The empty scenario injects a bad filter, not an HTTP failure.
                            rows = database.execute(
                                "SELECT title FROM recommendations WHERE ? = 0 ORDER BY rowid",
                                (int(fault == "empty"),),
                            ).fetchall()
                        items = [row[0] for row in rows]
                except sqlite3.Error:
                    span["status"] = "error"
                    span["detail"] = "Database query failed; returned 503"
                    return self.respond(503, {"error": "recommendations unavailable"})
                span["detail"] = f"Returned {len(items)} recommendation(s)" + ("; suspicious empty result" if not items else "")
                self.respond(200, {"recommendations": items})
    return Downstream


def make_gateway(store, downstream_url):
    class Gateway(JSONHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/traces/"):
                trace_id = parsed.path.split("/")[-1]
                return self.respond(200 if store.get(trace_id)["spans"] else 404, store.get(trace_id))
            if parsed.path == "/api/traces":
                return self.respond(200, store.recent())
            if parsed.path == "/api/run":
                fault = parse_qs(parsed.query).get("fault", ["healthy"])[0]
                if fault not in SCENARIOS:
                    return self.respond(400, {"error": "Unknown scenario"})
                trace_id = uuid.uuid4().hex
                with store.span(trace_id, "GET /api/run", "api-gateway") as root:
                    with store.span(trace_id, "fetch recommendations", "api-gateway", root["id"]) as fetch:
                        try:
                            request_started = time.monotonic()
                            request = Request(f"{downstream_url}/recommendations?fault={fault}", headers={
                                "X-Trace-ID": trace_id, "X-Parent-Span": fetch["id"]})
                            with urlopen(request, timeout=2) as response:
                                data = json.load(response)
                            elapsed = time.monotonic() - request_started
                            if elapsed > .2:
                                fetch["status"] = root["status"] = "warning"
                                fetch["detail"] = f"Downstream took {elapsed * 1000:.0f} ms"
                            elif not data.get("recommendations"):
                                fetch["status"] = "warning"
                                fetch["detail"] = "HTTP 200 but recommendation list is empty"
                                root["status"] = "warning"
                            else:
                                fetch["detail"] = f"Received {len(data['recommendations'])} results"
                            code = 200
                        except HTTPError as exc:
                            fetch["status"] = root["status"] = "error"
                            fetch["detail"] = f"Downstream HTTP {exc.code}"
                            data, code = {"error": "recommendation service failed"}, 502
                        except URLError as exc:
                            fetch["status"] = root["status"] = "error"
                            fetch["detail"] = f"Connection failure: {exc.reason}"
                            data, code = {"error": "recommendation service unreachable"}, 502
                return self.respond(code, {"trace_id": trace_id, "result": data,
                                           "trace": store.get(trace_id), "scenario": fault})
            file = ROOT / parsed.path.lstrip("/")
            if file.is_dir() or parsed.path == "/":
                file = ROOT / "index.html"
            if not file.resolve().is_relative_to(ROOT.resolve()) or not file.is_file():
                return self.respond(404, {"error": "Not found"})
            raw = file.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", {".html":"text/html", ".css":"text/css", ".js":"text/javascript"}.get(file.suffix,"text/plain"))
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
    return Gateway


def start_servers(gateway_port=8002, downstream_port=8003):
    store = TraceStore()
    downstream = ThreadingHTTPServer(("127.0.0.1", downstream_port), make_downstream(store))
    actual_downstream_port = downstream.server_address[1]
    gateway = ThreadingHTTPServer(("127.0.0.1", gateway_port),
                                  make_gateway(store, f"http://127.0.0.1:{actual_downstream_port}"))
    thread = threading.Thread(target=downstream.serve_forever, daemon=True)
    thread.start()
    return gateway, downstream, store


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8002)
    parser.add_argument("--downstream-port", type=int, default=8003)
    args = parser.parse_args()
    gateway, downstream, _ = start_servers(args.port, args.downstream_port)
    print(f"Open http://127.0.0.1:{gateway.server_address[1]}", flush=True)
    try:
        gateway.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        gateway.server_close()
        downstream.shutdown()
        downstream.server_close()
