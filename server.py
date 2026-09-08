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
import subprocess
import tempfile
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from application_version import application_build
from efforts import DEFINITIONS as EFFORT_DEFINITIONS, saved_effort, saved_profile
from categories import CATEGORIES, DEFINITIONS
from storage import BoardStore, Conflict
from publication import Publication
from versions import inspect, migrate, parse, PROTOCOL_VERSION
from configuration import resolve, configure_port, configure_mode, valid_port, board_context

ROOT = Path(__file__).resolve().parent
APPLICATION_BUILD = application_build(ROOT)
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
    inspect(data, 'board')
    require(data.get("schema_version") == 1, "Unsupported schema_version.")
    for collection in ("ideas", "todos"):
        require(isinstance(data.get(collection), list), f"{collection} must be a list.")
    ids = set()
    for collection, prefix in (("ideas", "I"), ("todos", "T")):
        for item in data[collection]:
            require(isinstance(item, dict), "Each entry must be an object.")
            ident = item.get("id")
            require(isinstance(ident, str) and re.fullmatch(r"(?:[A-Z][A-Z0-9]{0,11}_)?" + prefix + r"\d{4,}", ident), "Invalid entry ID.")
            require(ident not in ids, f"Duplicate ID: {ident}")
            ids.add(ident)
            string(item.get("author"), "Author", True)
            timestamp(item.get("date_entered"), "Date entered")
            if collection == "ideas":
                string(item.get("text"), "Idea text", True)
                if 'captured_system' in item:
                    require(isinstance(item['captured_system'], str) and re.fullmatch(r'[a-f0-9]{64}', item['captured_system']), 'Invalid capturing system.')
                continue
            inspect(item, f'todo {ident}')
            if 'workflow' in item:
                require(isinstance(item['workflow'], dict), 'workflow must be an object.')
                for key in ('run_id', 'system', 'repository', 'worktree', 'branch', 'base', 'phase', 'message'):
                    string(item['workflow'].get(key), 'workflow.'+key, True)
                run = item['workflow']
                require(bool(re.fullmatch(r'[a-f0-9]{32}', run['run_id'])), 'Invalid workflow run ID.')
                require(bool(re.fullmatch(r'[a-f0-9]{64}', run['system'])), 'Invalid workflow system.')
                require(run['branch'] == f"codex/{ident.lower()}-{run['run_id'][:8]}", 'Invalid workflow branch.')
                require(bool(re.fullmatch(r'[a-f0-9]{40,64}', run['base'])), 'Invalid workflow base.')
                require(run['phase'] in {'queued','merge_queued','implementing','ready','testing','tested','merging','restarting','done','implementation_failed','test_failed','merge_failed','push_failed','restart_failed','migration_required','migrating','recovering','resolving_conflict','testing_resolution','resolution_blocked','handoff_blocked'}, 'Invalid workflow phase.')
                if run['phase'] in ('queued', 'merge_queued'):
                    require(run.get('queued_action') in ('implement', 'retry', 'test', 'merge', 'migrate', 'recover', 'verify_existing'), 'Invalid queued action.')
                    require((run['phase'] == 'merge_queued') == (run['queued_action'] in ('merge', 'migrate', 'recover')), 'Invalid queue phase.')
                    timestamp(run.get('queued_at'), 'workflow.queued_at')
                from managed_completion import validate as validate_managed
                validate_managed(run)
                if 'external_completions' in run:
                    from external_completion import validate as validate_external
                    validate_external(run['external_completions'])
                if 'action_requests' in run:
                    require(isinstance(run['action_requests'], dict) and all(isinstance(k, str) and isinstance(v, str) for k,v in run['action_requests'].items()), 'Invalid action receipts.')
                for key in ('integration_commit', 'integration_tested_commit', 'deployment_commit', 'published_commit'):
                    if key in run:
                        require(isinstance(run[key], str) and bool(re.fullmatch(r'[a-f0-9]{40,64}', run[key])), 'Invalid workflow '+key)
                if run.get('preview_url'):
                    preview = urlsplit(run['preview_url'])
                    require(preview.scheme == 'http' and preview.hostname in ('localhost','127.0.0.1') and not preview.username and not preview.password, 'Invalid workflow preview URL.')
            for field in ("name", "description", "created_by"):
                string(item.get(field), field, True)
            for field in ("group", "closed_by", "date_closed", "pr_url", "commit_url", "commit_hash"):
                string(item.get(field), field)
            string(item.get('completion_summary', ''), 'Completion summary')
            dependencies = item.get('depends_on', [])
            require(isinstance(dependencies, list) and all(isinstance(v, str) for v in dependencies), 'depends_on must be a list of ticket IDs.')
            require(len(dependencies) == len(set(dependencies)) and ident not in dependencies, 'Duplicate or self dependency.')
            if inspect(item)[0] != 'read_only':
                saved_effort(item)
                saved_profile(item)
            category = item.get('category', '')
            string(category, 'Category')
            require(not category or category in CATEGORIES or inspect(item)[0] == 'read_only', 'Invalid category.')
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
    graph = {t['id']: set(t.get('depends_on', [])) for t in data['todos']}
    require(all(deps <= graph.keys() for deps in graph.values()), 'Unknown ticket dependency.')
    pending = dict(graph)
    while pending:
        ready = {key for key, deps in pending.items() if not deps.intersection(pending)}
        require(bool(ready), 'Ticket dependencies contain a cycle.')
        pending = {key: deps for key, deps in pending.items() if key not in ready}
    idea_ids = {item["id"] for item in data["ideas"]}
    foreign_ids = set()
    for todo in data["todos"]:
        require(set(todo["source_ideas"]) <= idea_ids, f"Unknown source idea in {todo['id']}.")
        require(isinstance(todo.get('source_refs', []), list), 'source_refs must be a list.')
        for ref in todo.get('source_refs', []):
            require(isinstance(ref, dict), 'Invalid source reference.')
            string(ref.get('project_id'), 'Source project ID', True)
            original = ref.get('idea')
            require(isinstance(original, dict), 'Source reference must retain the original idea.')
            validate(dict(schema_version=1, ideas=[original], todos=[]))
            identity = (ref['project_id'], original['id'])
            require(identity not in foreign_ids, 'Foreign idea already has a destination todo.')
            foreign_ids.add(identity)
    if previous is not None:
        old_todos = {t['id']: t for t in previous['todos']}
        for todo in data['todos']:
            old = old_todos.get(todo['id'], {})
            summary = todo.get('completion_summary', '')
            if todo['status'] == 'closed' and (old.get('status') != 'closed' or summary != old.get('completion_summary', '')):
                string(summary, 'Completion summary: describe outcome, verification and limitations', True)
    if previous:
        for collection in ("ideas", "todos"):
            current = {item["id"]: item for item in data[collection]}
            for old in previous[collection]:
                require(old["id"] in current, "Entries cannot be deleted; close todos instead.")
                new = current[old["id"]]
                for field in (("author", "date_entered", "text") if collection == "ideas" else ("author", "date_entered", "created_by")):
                    require(new[field] == old[field], f"Original {field} must be preserved for {old['id']}.")
                if collection == 'ideas':
                    require(new.get('captured_system') == old.get('captured_system'), 'Original capturing system must be preserved.')
                for field in ('source_refs', 'project_id', 'captured_system'):
                    if field in old:
                        require(new.get(field) == old[field], f'Original {field} must be preserved.')


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
        from instance_maintenance import Maintenance
        self.maintenance = Maintenance(self)
        self.processing = None
        self.workflow = None
        self.store = store
        self.token = secrets.token_urlsafe(32)
        self.publication = Publication(store) if isinstance(store, BoardStore) else None
        from aggregation import Aggregation
        self.aggregation = Aggregation(store) if getattr(store, 'context', {}).get('mode') == 'aggregation' else None

    def service_actions(self):
        self.maintenance.tick()
        if self.maintenance.quiescing:
            return
        if self.processing:
            self.processing.tick()
        if self.workflow and (not self.processing or self.processing.status()['status'] != 'running'):
            self.workflow.tick()

    def server_close(self):
        if self.workflow:
            self.workflow.close()
        if self.processing:
            self.processing.close()
        super().server_close()


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
            elif path == '/agent-advice.js':
                advice = json.loads((Path(__file__).resolve().parent / 'agent_advice.json').read_text())
                self.reply(200, ('const agentAdvice = ' + json.dumps(advice) + ';').encode(), 'text/javascript; charset=utf-8')
            elif path == '/api/maintenance':
                self.reply(200, self.server.maintenance.view())
            elif path == '/api/workflow/merge-review' and self.server.workflow:
                from bulk_merge import review
                self.reply(200, review(self.server.workflow))
            elif path == '/api/workflow' and self.server.workflow:
                self.reply(200, self.server.workflow.status())
            elif path == '/api/settings' and self.server.workflow and self.server.processing:
                from instance_settings import InstanceSettings
                self.reply(200, InstanceSettings(self.server.workflow, self.server.processing).view())
            elif path == '/api/processing' and self.server.processing:
                self.reply(200, self.server.processing.status())
            elif path == '/api/aggregate' and self.server.aggregation:
                self.reply(200, self.server.aggregation.view())
            elif path == "/api/publication" and self.server.publication:
                self.reply(200, self.server.publication.status())
            elif path == '/efforts-data.js':
                self.reply(200, ('const effortDefinitions = ' + json.dumps(EFFORT_DEFINITIONS) + ';').encode(), 'text/javascript; charset=utf-8')
            elif path == '/categories-data.js':
                self.reply(200, ('const categoryDefinitions = ' + json.dumps(DEFINITIONS) + ';').encode(), 'text/javascript; charset=utf-8')
            elif path in ("/", "/index.html", "/app.js", "/priority.js", "/processing.js", "/workflow.js", "/bulk-merge.js", "/maintenance.js", "/worker-capacity.js", "/instance-settings.js", "/aggregation.js", "/style.css", "/favicon.svg"):
                name = "index.html" if path == "/" else path[1:]
                mime = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml"}[Path(name).suffix]
                body = (ROOT / name).read_bytes()
                if name == 'index.html':
                    body = body.replace(b'__APPLICATION_BUILD__', APPLICATION_BUILD.encode('ascii'))
                self.reply(200, body, mime + "; charset=utf-8")
            else:
                self.reply(404, {"error": "Not found."})
        except (OSError, ValueError, TypeError, KeyError, Conflict) as error:
            self.reply(500, {"error": f"Could not read data: {error}. Your file has not been changed."})

    def do_PUT(self):
        with self.server.maintenance.mutation() as accepted:
            if not accepted:
                self.reply(503, {"error": "Restarting after drain. Retry this save when the instance returns."})
                return
            self.put()

    def put(self):
        if not self.local_request():
            return
        if self.path not in ("/api/maintenance", "/api/state", "/api/changes", "/api/history/retry", "/api/publication/refresh", "/api/publication/push", '/api/routes', '/api/source-record', '/api/source-priority', '/api/processing/start', '/api/processing/presence', '/api/workflow/action', '/api/workflow/merge-batch', '/api/workflow/settings', '/api/settings'):
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
            if 'protocol_version' in body:
                require(parse(body['protocol_version'])[0] == parse(PROTOCOL_VERSION)[0], 'Unsupported request protocol major. Update the client and Unfertig.')
                protocol_state, _ = inspect(body, 'request', PROTOCOL_VERSION, 'protocol_version')
                require(protocol_state != 'read_only', 'Newer minor request protocol: update Unfertig before writing.')
            if isinstance(self.server.store, BoardStore):
                if self.path == '/api/maintenance':
                    self.reply(200, self.server.maintenance.action(body))
                    return
                if self.path == '/api/settings':
                    require(self.server.workflow is not None and self.server.processing is not None, 'Settings are unavailable.')
                    from instance_settings import InstanceSettings
                    self.reply(200, InstanceSettings(self.server.workflow, self.server.processing).save(body))
                    return
                if self.path == '/api/workflow/settings':
                    require(self.server.workflow is not None, 'Workflow is unavailable.')
                    from worker_capacity import WorkerSettings
                    self.reply(200, WorkerSettings(self.server.workflow).save(body))
                    return
                if self.path in ('/api/workflow/action', '/api/workflow/merge-batch'):
                    require(self.server.workflow is not None, 'Workflow is unavailable.')
                    if not self.server.workflow.options['enabled']:
                        self.reply(403, {'error': 'Implementation workflow is disabled in configuration.'})
                        return
                    if self.path == '/api/workflow/merge-batch':
                        from bulk_merge import enqueue
                        self.reply(200, enqueue(self.server.workflow, body))
                    else:
                        self.reply(200, self.server.workflow.start(body))
                    return
                if self.path in ('/api/processing/start', '/api/processing/presence'):
                    require(self.server.processing is not None, 'Processing is unavailable.')
                    result = self.server.processing.start() if self.path.endswith('/start') else self.server.processing.presence(body)
                    self.reply(200, result)
                    return
                if self.path in ('/api/source-record', '/api/source-priority'):
                    require(self.server.aggregation is not None, 'Source operations require aggregation mode.')
                    from aggregation import SourceRejected, Unreachable
                    try:
                        method = self.server.aggregation.source_record if self.path == '/api/source-record' else self.server.aggregation.source_priority
                        self.reply(200, method(body))
                    except SourceRejected as error:
                        self.reply(error.status, {'error': str(error)})
                    except Unreachable as error:
                        self.reply(503, {'error': str(error)})
                    return
                if self.path == '/api/routes':
                    require(self.server.aggregation is not None, 'Routing requires aggregation mode.')
                    self.reply(200, self.server.aggregation.route(body))
                    return
                if self.path == "/api/publication/refresh":
                    self.reply(200, self.server.publication.refresh())
                    return
                if self.path == "/api/publication/push":
                    self.reply(200, self.server.publication.push(body.get("confirmation")))
                    return
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
            self.reply(500, {"error": 'Could not save settings. Check storage access and board history, then compare latest settings.' if self.path == '/api/settings' else f"Could not save: {error}"})


def main():
    parser = argparse.ArgumentParser(description="unfertig — local ideas and todos")
    parser.add_argument("--port", type=int, help="Override configured port (default 8765)")
    parser.add_argument("--configure-port", action="store_true", help="Ask for and save an instance port, then exit")
    parser.add_argument('--configure-mode', action='store_true', help='Configure standalone, embedded or explicit-source aggregation mode, then exit')
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
    store = BoardStore(configuration["path"], validate, git=not args.no_git, config=configuration["config"])
    store.context = board_context(configuration, ROOT)
    server = None
    try:
        if not store.path.exists() and not (args.configure_port or args.configure_mode or args.init or configuration["bootstrap"]) and not store.journal.exists():
            raise ValueError("Configured board is missing. Check its path or explicitly use --init.")
        serving = not any((args.apply, args.snapshot, args.retry_history, args.check, args.configure_mode, args.configure_port))
        store.acquire(cooperative=serving, service=serving)
        if args.configure_mode:
            store.preflight(); store.require_writable()
            if store.path.exists() and store.read()[0]['todos']:
                raise ValueError('Mode setup requires an empty todo collection; use configuration directly for non-aggregation changes.')
            configure_mode(configuration, ROOT)
            store.close()
            return
        if args.configure_port:
            store.preflight()
            store.require_writable()
            configure_port(configuration, ROOT)
            if not args.check:
                store.close()
                return
        if args.check:
            # Validation is read-only; do not migrate or retry history.
            store.preflight()
            if json.loads(store.path.read_bytes()).get('schema_version') == 1:
                legacy = store.document(store.path)
                legacy['todos'] = [migrate(t, 'todo') for t in legacy['todos']]
                validate(legacy)
            else:
                if store.journal.exists():
                    raise ValueError('Interrupted transaction: start the server to recover first.')
                store.read()
            print(f"Data is valid: {store.path}")
            store.close()
            return
        if not (args.apply or args.snapshot or args.retry_history):
            # Capture the loaded service revision once. A later checkout change
            # must not make an old process claim to serve the new implementation.
            try:
                revision = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'],
                                          capture_output=True, text=True, timeout=10)
                store.context['runtime_commit'] = revision.stdout.strip() if revision.returncode == 0 else ''
            except (OSError, subprocess.SubprocessError):
                store.context['runtime_commit'] = ''  # Archive installs can serve, but cannot attest a Git deployment.
            server = Server(("127.0.0.1", port), store)
        if not store.path.exists() and not store.journal.exists():
            store.create_starter()
        store.initialize()
        if configuration['mode'] == 'aggregation' and store.read()[0]['todos']:
            raise ValueError('Aggregation requires an inbox with no todos. Existing todos must not be relocated implicitly.')
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
    from processing import Processor
    server.processing = Processor(store, url, configuration["processing"])
    from workflow import Workflow
    server.workflow = Workflow(store, url, configuration["workflow"], configuration["processing"])
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
    return server.maintenance.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
