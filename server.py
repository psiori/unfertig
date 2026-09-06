# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Small local file-backed idea board. Run with uv run --script server.py."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from storage import BoardStore, Conflict
from configuration import resolve, configure_port, valid_port

ROOT = Path(__file__).resolve().parent
MAX_BYTES = 5_000_000
STATUSES = {"open", "started", "closed"}
PRIORITIES = {"low", "normal", "high", "urgent"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def string(value, name, nonempty=False):
    require(isinstance(value, str), f"{name} must be text.")
    require(len(value) <= 100_000, f"{name} is too long.")
    if nonempty:
        require(bool(value.strip()), f"{name} cannot be empty.")


def timestamp(value, name):
    string(value, name, True)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        require(parsed.tzinfo is not None, f"{name} needs a timezone.")
    except (ValueError, TypeError):
        raise ValueError(f"{name} must be an ISO timestamp with timezone.") from None


def validate(data, previous=None):
    require(isinstance(data, dict), "Data must be a JSON object.")
    require(data.get("schema_version") == 1, "Unsupported schema_version.")
    for collection in ("ideas", "todos"):
        require(isinstance(data.get(collection), list), f"{collection} must be a list.")
    ids = set()
    for collection, prefix in (("ideas", "I"), ("todos", "T")):
        for item in data[collection]:
            require(isinstance(item, dict), "Each entry must be an object.")
            ident = item.get("id")
            require(isinstance(ident, str) and re.fullmatch(prefix + r"\d{4,}", ident), "Invalid entry ID.")
            require(ident not in ids, f"Duplicate ID: {ident}")
            ids.add(ident)
            string(item.get("author"), "Author", True)
            timestamp(item.get("date_entered"), "Date entered")
            if collection == "ideas":
                string(item.get("text"), "Idea text", True)
                continue
            for field in ("name", "description", "created_by"):
                string(item.get(field), field, True)
            for field in ("group", "closed_by", "date_closed", "pr_url", "commit_url", "commit_hash"):
                string(item.get(field), field)
            timestamp(item.get("updated_at"), "Updated at")
            require(isinstance(item.get("priority"), str) and item["priority"] in PRIORITIES, "Invalid priority.")
            require(isinstance(item.get("status"), str) and item["status"] in STATUSES, "Invalid status.")
            for field in ("tags", "source_ideas"):
                require(isinstance(item.get(field), list), f"{field} must be a list.")
                for value in item[field]:
                    string(value, field, True)
                require(len(set(item[field])) == len(item[field]), f"Duplicate {field}.")
            if item["status"] == "closed":
                string(item["closed_by"], "Closed by", True)
                timestamp(item["date_closed"], "Date closed")
            else:
                require(not item["closed_by"] and not item["date_closed"], "Open/started todos must have empty closure fields.")
            for field in ("pr_url", "commit_url"):
                if item[field]:
                    parsed = urlsplit(item[field])
                    require(parsed.scheme == "https" and bool(parsed.netloc), f"{field} must be an HTTPS URL.")
            require(not item["commit_hash"] or re.fullmatch(r"[a-fA-F0-9]{7,64}", item["commit_hash"]), "Commit hash must contain 7–64 hexadecimal characters.")
    idea_ids = {item["id"] for item in data["ideas"]}
    for todo in data["todos"]:
        require(set(todo["source_ideas"]) <= idea_ids, f"Unknown source idea in {todo['id']}.")
    if previous:
        for collection in ("ideas", "todos"):
            current = {item["id"]: item for item in data[collection]}
            for old in previous[collection]:
                require(old["id"] in current, "Entries cannot be deleted; close todos instead.")
                new = current[old["id"]]
                for field in (("author", "date_entered", "text") if collection == "ideas" else ("author", "date_entered", "created_by")):
                    require(new[field] == old[field], f"Original {field} must be preserved for {old['id']}.")


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()

    def read(self):
        raw = self.path.read_bytes()
        require(len(raw) <= MAX_BYTES, "Data file exceeds 5 MB.")
        data = json.loads(raw)
        validate(data)
        return data, hashlib.sha256(raw).hexdigest()

    def save(self, data, revision):
        with self.lock:
            previous, current = self.read()
            if current != revision:
                raise Conflict("The file changed elsewhere. Copy your draft, then load the latest version.")
            validate(data, previous)
            raw = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()
            require(len(raw) <= MAX_BYTES, "Data file exceeds 5 MB.")
            fd, name = tempfile.mkstemp(prefix=".data-", suffix=".tmp", dir=self.path.parent)
            try:
                with os.fdopen(fd, "wb") as file:
                    file.write(raw)
                    file.flush()
                    os.fsync(file.fileno())
                if self.read()[1] != revision:
                    raise Conflict("The file changed during saving. Load the latest version.")
                os.replace(name, self.path)
            finally:
                if os.path.exists(name):
                    os.unlink(name)
            return hashlib.sha256(raw).hexdigest()


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, store):
        super().__init__(address, Handler)
        self.store = store
        self.token = secrets.token_urlsafe(32)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, status, body, mime="application/json; charset=utf-8"):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(body)

    def local_request(self):
        port = self.server.server_port
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host") not in allowed:
            self.reply(403, {"error": "Only local requests are accepted."})
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in {"http://" + host for host in allowed}:
            self.reply(403, {"error": "Cross-origin requests are not accepted."})
            return False
        return True

    def do_GET(self):
        if not self.local_request():
            return
        path = urlsplit(self.path).path
        try:
            if path == "/api/state":
                if isinstance(self.server.store, BoardStore):
                    self.reply(200, dict(self.server.store.snapshot(), token=self.server.token))
                else:
                    data, revision = self.server.store.read()
                    self.reply(200, {"data": data, "revision": revision, "token": self.server.token})
            elif path in ("/", "/index.html", "/app.js", "/style.css", "/favicon.svg"):
                name = "index.html" if path == "/" else path[1:]
                mime = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml"}[Path(name).suffix]
                self.reply(200, (ROOT / name).read_bytes(), mime + "; charset=utf-8")
            else:
                self.reply(404, {"error": "Not found."})
        except (OSError, ValueError, TypeError, KeyError) as error:
            self.reply(500, {"error": f"Could not read data: {error}. Your file has not been changed."})

    def do_PUT(self):
        if not self.local_request():
            return
        if self.path not in ("/api/state", "/api/changes", "/api/history/retry"):
            self.reply(404, {"error": "Not found."})
            return
        if not secrets.compare_digest(self.headers.get("X-Board-Token", ""), self.server.token):
            self.reply(403, {"error": "Session expired. Reload the page before saving."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            require(0 < length <= MAX_BYTES, "Request must be between 1 byte and 5 MB.")
            body = json.loads(self.rfile.read(length))
            require(isinstance(body, dict), "Expected a JSON object.")
            if isinstance(self.server.store, BoardStore):
                if self.path == "/api/state":
                    raise Conflict("This server uses record edits. Reload the app; whole-board PUT is no longer supported.")
                if self.path == "/api/history/retry":
                    with self.server.store.lock:
                        self.server.store.commit_pending()
                        self.reply(200, dict(self.server.store.snapshot(), token=self.server.token))
                else:
                    self.reply(200, dict(self.server.store.mutate(body), token=self.server.token))
            else:
                revision = self.server.store.save(body.get("data"), body.get("revision"))
                self.reply(200, {"revision": revision})
        except Conflict as error:
            self.reply(409, {"error": str(error)})
        except (ValueError, TypeError, KeyError) as error:
            self.reply(400, {"error": str(error)})
        except OSError as error:
            self.reply(500, {"error": f"Could not save: {error}"})


def main():
    parser = argparse.ArgumentParser(description="unfertig — local ideas and todos")
    parser.add_argument("--port", type=int, help="Override configured port (default 8765)")
    parser.add_argument("--configure-port", action="store_true", help="Ask for and save an instance port, then exit")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--data", type=Path, help="Board file; relative to config directory, or app root without config")
    parser.add_argument("--config", type=Path, help="Explicit configuration JSON")
    parser.add_argument("--state-dir", type=Path, help="External state directory containing config/config.json (or UNFERTIG_STATE_DIR)")
    parser.add_argument("--init", action="store_true", help="Initialize a missing board with one onboarding todo")
    parser.add_argument("--no-git", action="store_true", help="Disable automatic commits for temporary test data")
    parser.add_argument("--snapshot", action="store_true", help="Print an offline snapshot with record revisions")
    parser.add_argument("--retry-history", action="store_true", help="Retry pending local Git history offline")
    parser.add_argument("--apply", type=Path, help="Apply a record-change request offline under the exclusive lock")
    parser.add_argument("--check", action="store_true", help="Validate data and exit")
    args = parser.parse_args()
    try:
        configuration = resolve(ROOT, args.config, args.data, args.no_git, args.state_dir)
        port = valid_port(args.port) if args.port is not None else configuration["port"]
    except (OSError, ValueError) as error:
        parser.exit(1, f"Could not resolve board: {error}\n")
    store = BoardStore(configuration["path"], validate, git=not args.no_git)
    store.context = {"config": str(configuration["config"] or ""), "app_root": str(ROOT), "process": str(ROOT / "PROCESS.md"),
                     "data": str(store.path), "todos": str(store.root / "todos"),
                     "repository": str(configuration["repository"] or ""), "mode": configuration["mode"],
                     "project_name": configuration["project_name"]}
    server = None
    try:
        if not store.path.exists() and not (args.configure_port or args.init or configuration["bootstrap"]) and not store.journal.exists():
            raise ValueError("Configured board is missing. Check its path or explicitly use --init.")
        store.acquire()
        if args.configure_port:
            configure_port(configuration, ROOT)
            if not args.check:
                store.close()
                return
        if args.check:
            # Validation is read-only; do not migrate or retry history.
            if json.loads(store.path.read_bytes()).get('schema_version') == 1:
                Store(store.path).read()
            else:
                if store.journal.exists():
                    raise ValueError('Interrupted transaction: start the server to recover first.')
                store.read()
            print(f"Data is valid: {store.path}")
            store.close()
            return
        if not (args.apply or args.snapshot or args.retry_history):
            server = Server(("127.0.0.1", port), store)
        if not store.path.exists() and not store.journal.exists():
            store.create_starter()
        store.initialize()
        if args.snapshot or args.retry_history:
            print(json.dumps(store.snapshot(), ensure_ascii=False))
            store.close()
            return
        if args.apply:
            print(json.dumps(store.mutate(json.loads(args.apply.read_bytes())), ensure_ascii=False))
            store.close()
            return
    except (OSError, ValueError, Conflict, EOFError) as error:
        store.close()
        if server:
            server.server_close()
        parser.exit(1, f"\nCould not start: {error}\nIf the port is busy, close the other app window's terminal or use --port 8766.\nSee {ROOT / 'README.md'} for help.\n")
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"\n  unfertig\n  {url}\n\n  Saving to {store.path}\n  Keep this terminal open. Press Ctrl+C to stop.\n", flush=True)
    if not args.no_browser:
        threading.Timer(0.4, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBoard stopped. Saved ideas and todos are safe on disk.")
    finally:
        server.server_close()
        store.close()


if __name__ == "__main__":
    main()
