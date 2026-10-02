"""
Retrieval as a small HTTP service, so every agent call shares one loaded index.

    python -m asic_agent kb-service          # 127.0.0.1:$RAG_PORT (8090)

    GET  /health              {"ok": true, "mode": "hybrid"|"keyword-only", "meta": {...}}
    GET  /query?q=...&k=5     {"hits": [Hit, ...], "mode": ...}
    POST /reindex             rebuild from config/project.json sources + confirmed
                              patterns, then reload

Bound to 127.0.0.1 only. If no index exists yet it is built on start.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import log, settings
from ..llm import LLMError
from .embeddings import get_embedder
from .ingest import build_sources
from .retriever import Retriever

_log = log.get("knowledge.service")
_lock = threading.Lock()
_retriever: Retriever | None = None


def project_sources() -> list[tuple[Path, str]]:
    """knowledge_sources from config/project.json — the client's data."""
    p = settings.load_project()
    return [(settings.project_path(s["path"]), s.get("kind", "auto"))
            for s in p.get("knowledge_sources", [])]


def load() -> Retriever:
    global _retriever
    r = Retriever.load()
    with _lock:
        _retriever = r
    return r


def reindex() -> dict:
    from .client import export_patterns       # past fixes of the CURRENT design only
    export_patterns()
    meta = build_sources(project_sources(), [settings.PATTERNS_DIR], get_embedder(),
                         settings.INDEX_DIR, log=_log.info)
    r = load()
    meta["mode"] = r.mode
    return meta


def ensure_current() -> None:
    """Rebuild when the active project's sources differ from the loaded index.

    The project file can change behind the service's back (copied over, edited
    by hand). Answering from the previous design's index would hand the agents
    another design's spec and testbench — this happened on 2 Oct, when three
    apb_timer requests were analysed against apb_gpio's knowledge base."""
    with _lock:
        r = _retriever
    want = sorted(str(p) for p, _ in project_sources())
    have = sorted(s["path"] for s in (r.store.meta.get("sources", []) if r else []))
    if want != have:
        _log.info("project sources changed: rebuilding the index")
        reindex()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        _log.debug("%s " + fmt, self.address_string(), *args)

    def send(self, obj, status=200):
        b = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path in ("/health", "/query"):
            try:
                ensure_current()
            except Exception:  # noqa: BLE001 - keep answering from what is loaded
                _log.exception("rebuild for the changed project failed")
        with _lock:
            r = _retriever
        if u.path == "/health":
            return self.send({"ok": r is not None, "mode": r.mode if r else None,
                              "meta": r.store.meta if r else None})
        if u.path == "/query":
            if r is None:
                return self.send({"error": "index not loaded"}, 503)
            text = (q.get("q") or [""])[0][:4000]
            try:
                k = max(1, min(int((q.get("k") or ["5"])[0]), 50))
            except ValueError:
                return self.send({"error": "k must be an integer"}, 400)
            try:
                hits = r.retrieve(text, k=k)
            except LLMError as e:
                # The embeddings API failed for this query: answer from the
                # keyword index rather than not at all, and say so.
                _log.warning("vector search failed (%s); keyword-only for this query", e)
                hits = r.retrieve(text, k=k, vector_weight=0.0, keyword_weight=1.0, keyword_only=True)
            out = []
            for h in hits:
                d = h.to_dict()
                d["location"] = f"{Path(h.source_path).name}:{h.line_start}-{h.line_end}"
                out.append(d)
            return self.send({"hits": out, "mode": r.mode})
        self.send({"error": "unknown endpoint"}, 404)

    def do_POST(self):
        if urlparse(self.path).path == "/reindex":
            try:
                return self.send(reindex())
            except Exception as e:  # noqa: BLE001 - reported to the caller, logged here
                _log.exception("reindex failed")
                return self.send({"error": f"{type(e).__name__}: {e}"}, 500)
        self.send({"error": "unknown endpoint"}, 404)


def main() -> int:
    log.setup()
    port = int(os.environ.get("RAG_PORT", "8090"))
    try:
        load()
        ensure_current()
    except FileNotFoundError:
        _log.info("no index yet: building from config/project.json")
        reindex()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    _log.info("knowledge service on http://127.0.0.1:%d (%s chunks, %s)", port,
              _retriever.store.meta.get("n_chunks"), _retriever.mode)
    srv.serve_forever()
    return 0
