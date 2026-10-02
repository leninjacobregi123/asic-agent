"""
Flow Console — the web application for the agent flow.

    scripts/start_app.sh      then open http://localhost:8080

Everything the terminal flow does, from the browser: start a run, watch each
stage live, approve every tool invocation, choose between spec readings,
supply a human fix when a run escalates, and inspect results.

The server is a thin layer over the existing pieces and adds no behaviour of
its own: runs are executed by scripts/run_flow.sh (orchestrator driver, --web),
approvals travel through runs/<id>/gate.json and gate_answer.json, and run
data is read from runs/<id>/manifest.json. Standard library only, bound to
127.0.0.1. API keys never leave the server: the browser sees provider and model names.
"""

from __future__ import annotations

import hmac
import json
import mimetypes
import os
import re
import signal
import subprocess
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import log, settings
from ..knowledge import client as knowledge
from ..llm import LLMConfigError, load_config
from . import runs_view

ROOT = settings.ROOT
STATIC = Path(__file__).resolve().parent / "static"
RUNS = settings.RUNS_DIR
CONSOLE = settings.CONSOLE_DIR
RUN_FLOW = ROOT / "scripts" / "run_flow.sh"
_log = log.get("web")

RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
TOKEN_COOKIE = "asic_token"

# Sent with every response. Scripts only from this server; styles also inline
# (style= attributes) and Google Fonts; no framing, no plugins.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'"),
}


def api_parts(path: str) -> list[str]:
    """Path segments; /api/v1/... is the documented, versioned form of /api/...
    (the browser app uses the short form)."""
    parts = [p for p in path.split("/") if p]
    return ["api", *parts[2:]] if parts[:2] == ["api", "v1"] else parts


def access_token() -> str:
    """ASIC_AGENT_TOKEN (environment or secrets.env). Required when the server
    listens beyond loopback; optional otherwise."""
    return os.environ.get("ASIC_AGENT_TOKEN", "").strip()


def scenarios() -> dict[str, dict]:
    """Verification-run scenarios: the unmodified design plus the active
    project's fault_scenarios."""
    out = {"clean": {"id": "clean", "label": "Unmodified design",
                     "description": "The design as configured, no fault added."}}
    for sc in settings.load_project().get("fault_scenarios", []):
        out[sc["id"]] = {k: sc.get(k) for k in ("id", "label", "description")}
    return out

_lock = threading.Lock()
_active: dict[str, subprocess.Popen] = {}


# ---------------------------------------------------------------------------
# Runs and processes
# ---------------------------------------------------------------------------

def active_run() -> str | None:
    with _lock:
        for rid, p in list(_active.items()):
            if p.poll() is None:
                return rid
            del _active[rid]
    return None


def llm_status() -> dict:
    """What runs will use, and what a person may choose. Never a key."""
    try:
        cfg = load_config()
    except LLMConfigError as e:
        return {"available": False, "error": str(e), "choices": [], "default": None,
                "embeddings": None}
    choices = cfg.chat_choices()
    default = None
    if cfg.chat and cfg.chat.provider.has_key():
        default = {"provider": cfg.chat.provider.name, "model": cfg.chat.model}
        if default not in choices:
            choices.insert(0, default)
    emb = cfg.embeddings
    return {"available": default is not None or bool(choices), "default": default, "choices": choices,
            "embeddings": f"{emb.provider.name}:{emb.model}" if emb else None}


def model_env(b: dict) -> dict[str, str] | None:
    """LLM_PROVIDER/LLM_MODEL for a run, only from the configured choices.
    {} means the configured default; None means the choice is not allowed."""
    want = {"provider": b.get("provider"), "model": b.get("model")}
    if not want["provider"]:
        return {}
    if want not in llm_status()["choices"]:
        return None
    return {"LLM_PROVIDER": want["provider"], "LLM_MODEL": want["model"]}


def start_process(run_id: str, args: list[str], env_extra: dict[str, str]) -> None:
    CONSOLE.mkdir(parents=True, exist_ok=True)
    log = (CONSOLE / f"{run_id}.log").open("ab")
    env = {**os.environ, "PYTHONUNBUFFERED": "1", **env_extra}
    p = subprocess.Popen(["bash", str(RUN_FLOW), *args], cwd=ROOT,
                         stdout=log, stderr=subprocess.STDOUT, env=env,
                         start_new_session=True)
    with _lock:
        _active[run_id] = p


def summarize(mpath: Path) -> dict:
    m = json.loads(mpath.read_text())
    st = m.get("stages", {})
    so = st.get("signoff", {})
    tests = st.get("verify_rtl", {}).get("test_cases", [])
    inject = next((h.get("kind") for h in m.get("history", []) if h["event"] == "fault_injected"), None)
    human = any(e.get("author") == "human" for e in st.get("rtl", {}).get("edits", []))
    if m.get("kind") == "request":
        inject = "request"
    return {
        "kind": m.get("kind", "verification"), "request": (m.get("request") or "")[:200],
        "verdict": (st.get("analysis") or {}).get("verdict"),
        "run_id": m["run_id"], "started": m.get("started"), "finished": m.get("finished"),
        "wall_seconds": m.get("wall_seconds"), "stage": m.get("current_stage"),
        "llm": m.get("llm"), "scenario": inject or "clean", "base_run": m.get("base_run"),
        "human_fix": human,
        "tests_passed": sum(t["status"] == "pass" for t in tests), "tests_total": len(tests),
        "signoff_pass": so.get("signoff_pass"), "setup_wns_ns": so.get("setup_wns_ns"),
        "route": next((h.get("route_to") for h in reversed(m.get("history", []))
                       if h.get("route_to")), None),
    }


def all_runs() -> list[dict]:
    out = []
    for mp in RUNS.glob("*/manifest.json"):
        try:
            m = json.loads(mp.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if "synth" not in m.get("stages", {}):
            continue  # records from the early driver have no back-end stages
        s = summarize(mp)
        s["running"] = s["run_id"] == active_run()
        out.append(s)
    out.sort(key=lambda r: r["started"] or "", reverse=True)
    return out


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "FlowConsole/1.0"

    def log_message(self, fmt, *args):  # quiet; errors are logged
        if len(args) > 1 and str(args[1]).startswith(("4", "5")):
            _log.warning("%s - %s", self.address_string(), fmt % args)

    # -- helpers ----------------------------------------------------------
    def end_headers(self):
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        super().end_headers()

    # -- access control ------------------------------------------------------
    def authorized(self) -> bool:
        """Token check when ASIC_AGENT_TOKEN is set: Bearer header or the
        HttpOnly cookie set by visiting /?token=<token> once."""
        token = access_token()
        if not token:
            return True
        got = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
        if not got:
            for part in (self.headers.get("Cookie") or "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == TOKEN_COOKIE:
                    got = v
        return bool(got) and hmac.compare_digest(got, token)

    def same_origin(self) -> bool:
        """Reject cross-site POSTs (CSRF): a browser always sends Origin on
        them, and a plain HTML form cannot send application/json."""
        if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
            return False
        origin = self.headers.get("Origin")
        return origin is None or urlparse(origin).netloc == self.headers.get("Host")

    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_bytes(self, data: bytes, ctype: str, cache: bool = False):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "max-age=3600" if cache else "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def error(self, status, msg):
        self.send_json({"error": msg}, status)

    def body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 2_000_000:
            raise ValueError("request too large")
        return json.loads(self.rfile.read(n) or b"{}")

    def run_dir(self, rid: str) -> Path | None:
        if not RUN_ID.match(rid):
            return None
        d = RUNS / rid
        return d if (d / "manifest.json").exists() else None

    # -- GET --------------------------------------------------------------
    def do_GET(self):
        url = urlparse(self.path)
        parts = api_parts(url.path)
        q = parse_qs(url.query)
        token = access_token()
        if token and q.get("token") and hmac.compare_digest(q["token"][0], token):
            # One-time login link: set the cookie, drop the token from the URL.
            self.send_response(303)
            self.send_header("Set-Cookie", f"{TOKEN_COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/")
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if parts[:1] == ["api"] and parts[1:] == ["health"]:
            return self.send_json({"ok": True, "active": active_run()})
        if parts[:1] == ["api"] and not self.authorized():
            return self.error(401, "not signed in: open the link with ?token=… once")
        try:
            if not parts or parts[0] not in ("api",):
                return self.static(url.path)
            if parts[1:] == ["overview"]:
                return self.send_json({
                    "toolchain": runs_view.toolchain(), "llm": llm_status(),
                    "project": settings.load_project().get("name"),
                    "scenarios": list(scenarios().values()),
                    "active": active_run(), "runs": all_runs(),
                    "server_time": datetime.now().isoformat(timespec="seconds")})
            if parts[1:] == ["project"]:
                pj = settings.PROJECT_FILE
                return self.send_json({"text": pj.read_text(), "project": json.loads(pj.read_text())})
            if parts[1:] == ["knowledge", "status"]:
                up = knowledge.available()
                meta = knowledge._get("/health", 2).get("meta") if up else None
                pats = sorted(p.name for p in knowledge.PATTERNS.glob("*.md")) \
                    if knowledge.PATTERNS.exists() else []
                return self.send_json({"available": up, "meta": meta, "patterns": pats})
            if parts[1:] == ["knowledge", "search"]:
                text = (q.get("q") or [""])[0].strip()
                k = min(int((q.get("k") or ["8"])[0]), 20)
                if not text:
                    return self.send_json({"hits": []})
                if not knowledge.available():
                    return self.error(503, "the knowledge base service is not running "
                                           "(it starts with scripts/start_app.sh)")
                return self.send_json({"hits": knowledge.retrieve(text, k=k, min_score=0.0)})
            if parts[1:] == ["openapi.json"]:
                return self.send_bytes((Path(__file__).parent / "openapi.json").read_bytes(),
                                       "application/json")
            if parts[1:] == ["runs"]:
                return self.send_json({"runs": all_runs(), "active": active_run()})
            if parts[1:] == ["models"]:
                from ..llm import manage
                try:
                    providers = manage.status()
                except LLMConfigError as e:
                    return self.error(500, str(e))
                return self.send_json({"providers": providers, **llm_status()})
            if parts[1:] == ["knowledge", "record"]:
                kb = settings.KNOWLEDGE_RECORD
                return self.send_json({"records": [json.loads(l) for l in kb.read_text().splitlines()]
                                       if kb.exists() else []})
            if len(parts) >= 3 and parts[1] == "runs":
                rid = parts[2]
                d = self.run_dir(rid)
                if d is None:
                    return self.error(404, f"no run {rid}")
                if len(parts) == 3:
                    rec = runs_view.run_record(d / "manifest.json")
                    rec["summary"] = summarize(d / "manifest.json")
                    rec["running"] = rid == active_run()
                    rec["gate"] = json.loads((d / "gate.json").read_text()) \
                        if (d / "gate.json").exists() and rec["running"] else None
                    return self.send_json(rec)
                if parts[3] == "console":
                    f = CONSOLE / f"{rid}.log"
                    off = int((q.get("offset") or ["0"])[0])
                    data = f.read_bytes() if f.exists() else b""
                    return self.send_json({"text": data[off:].decode("utf-8", "replace"),
                                           "offset": len(data), "running": rid == active_run()})
                if parts[3] == "image" and len(parts) == 5 and re.match(r"^[a-z_]+$", parts[4]):
                    top = json.loads((d / "manifest.json").read_text()).get("target_block", "")
                    img = runs_view.layout_dir(d, top) / f"{parts[4]}.webp.png"
                    if not img.is_file():
                        return self.error(404, "no image")
                    data = img.read_bytes()
                    ctype = "image/webp" if data[8:12] == b"WEBP" else "image/png"
                    return self.send_bytes(data, ctype, cache=True)
                if parts[3] == "file" and "path" in q:
                    # Text artifacts inside the run folder only (diffs, specs, logs).
                    f = (d / q["path"][0]).resolve()
                    if d.resolve() not in f.parents or not f.is_file() or f.stat().st_size > 3_000_000:
                        return self.error(404, "not available")
                    return self.send_bytes(f.read_bytes(), "text/plain; charset=utf-8")
            return self.error(404, "unknown endpoint")
        except Exception as e:  # noqa: BLE001 — surface, don't crash the server
            _log.exception("GET %s failed", self.path)
            return self.error(500, f"{type(e).__name__}: {e}")

    def static(self, path: str):
        rel_path = path.lstrip("/") or "index.html"
        f = (STATIC / rel_path).resolve()
        if STATIC.resolve() not in f.parents or not f.is_file():
            f = STATIC / "index.html"   # client-side routes
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self.send_bytes(f.read_bytes(), ctype)

    # -- POST -------------------------------------------------------------
    def do_POST(self):
        parts = api_parts(urlparse(self.path).path)
        if not self.authorized():
            return self.error(401, "not signed in")
        if not self.same_origin():
            return self.error(403, "cross-site or non-JSON request refused")
        try:
            b = self.body()
        except (ValueError, json.JSONDecodeError) as e:
            return self.error(400, str(e))
        try:
            if parts == ["api", "runs"]:
                return self.start_run(b)
            if parts == ["api", "requests"]:
                return self.start_request(b)
            if parts == ["api", "project"]:
                return self.save_project(b)
            if parts == ["api", "models", "test"]:
                from ..llm import manage
                prov, model = str(b.get("provider", "")), str(b.get("model", ""))
                if not re.fullmatch(r"[A-Za-z0-9._-]{1,40}", prov) or \
                        not re.fullmatch(r"[A-Za-z0-9._:/@+-]{1,120}", model):
                    return self.error(400, "invalid provider or model name")
                return self.send_json(manage.test(prov, model, bool(b.get("embeddings"))))
            if parts == ["api", "knowledge", "reindex"]:
                if active_run():
                    return self.error(409, "wait for the current run to finish")
                meta = knowledge.reindex()
                return self.send_json(meta) if meta else \
                    self.error(503, "the knowledge base service is not running")
            if len(parts) == 4 and parts[:2] == ["api", "runs"]:
                rid, action = parts[2], parts[3]
                d = self.run_dir(rid)
                if d is None:
                    return self.error(404, f"no run {rid}")
                if action == "gate":
                    g = d / "gate.json"
                    if not g.exists():
                        return self.error(409, "nothing is waiting for an answer")
                    gate = json.loads(g.read_text())
                    if b.get("id") != gate["id"]:
                        return self.error(409, "that question has already been answered")
                    ans = {"id": gate["id"], "by": (b.get("by") or "web user")[:40],
                           "at": datetime.now().isoformat(timespec="seconds")}
                    if gate["kind"] == "approve":
                        ans["approve"] = bool(b.get("approve"))
                    else:
                        ans["choice"] = b.get("choice") if b.get("choice") in gate["options"] else None
                    tmp = d / "gate_answer.json.tmp"
                    tmp.write_text(json.dumps(ans))
                    tmp.replace(d / "gate_answer.json")
                    return self.send_json({"ok": True})
                if action == "resume":
                    return self.resume(rid, d, b)
                if action == "stop":
                    with _lock:
                        p = _active.get(rid)
                    if not p or p.poll() is not None:
                        return self.error(409, "run is not active")
                    os.killpg(p.pid, signal.SIGTERM)
                    return self.send_json({"ok": True})
            return self.error(404, "unknown endpoint")
        except Exception as e:  # noqa: BLE001
            _log.exception("POST %s failed", self.path)
            return self.error(500, f"{type(e).__name__}: {e}")

    def start_run(self, b: dict):
        if active_run():
            return self.error(409, f"run {active_run()} is still in progress")
        rid = (b.get("run_id") or "").strip() or datetime.now().strftime("run-%Y%m%d-%H%M%S")
        if not RUN_ID.match(rid):
            return self.error(400, "run name: letters, digits, '.', '_' or '-' only")
        if (RUNS / rid).exists():
            return self.error(409, f"a run named {rid} already exists")
        scenario = b.get("scenario", "clean")
        if scenario not in scenarios():
            return self.error(400, "unknown scenario")
        args = ["--run-id", rid, "--web"]
        if b.get("mode") == "auto":
            args.append("--auto-approve")
        if scenario != "clean":
            args += ["--inject", scenario]
        if b.get("base_run"):
            if not self.run_dir(b["base_run"]):
                return self.error(400, "unknown base run")
            args += ["--base-run", b["base_run"]]
        env: dict[str, str] = {}
        if b.get("llm") == "none":
            args.append("--no-llm")
        else:
            env = model_env(b)
            if env is None:
                return self.error(400, "that provider/model is not configured")
        start_process(rid, args, env)
        return self.send_json({"ok": True, "run_id": rid})

    def start_request(self, b: dict):
        if active_run():
            return self.error(409, f"run {active_run()} is still in progress")
        text = (b.get("request") or "").strip()
        if len(text) < 15:
            return self.error(400, "describe the change in a sentence or two")
        rid = (b.get("run_id") or "").strip() or datetime.now().strftime("req-%Y%m%d-%H%M%S")
        if not RUN_ID.match(rid):
            return self.error(400, "run name: letters, digits, '.', '_' or '-' only")
        if (RUNS / rid).exists():
            return self.error(409, f"a run named {rid} already exists")
        CONSOLE.mkdir(parents=True, exist_ok=True)
        f = CONSOLE / f"{rid}.request.txt"
        f.write_text(text[:4000] + "\n")
        args = ["--run-id", rid, "--web", "--request-file", str(f)]
        if b.get("mode") == "auto":
            args.append("--auto-approve")
        if b.get("base_run"):
            if not self.run_dir(b["base_run"]):
                return self.error(400, "unknown base run")
            args += ["--base-run", b["base_run"]]
        env = model_env(b)
        if env is None:
            return self.error(400, "that provider/model is not configured")
        start_process(rid, args, env)
        return self.send_json({"ok": True, "run_id": rid})

    def save_project(self, b: dict):
        if active_run():
            return self.error(409, "wait for the current run to finish")
        try:
            pj = json.loads(b.get("text") or "")
        except json.JSONDecodeError as e:
            return self.error(400, f"not valid JSON: {e}")
        paths = [pj.get("design", {}).get("rtl"), pj.get("design", {}).get("spec"),
                 pj.get("testbench", {}).get("file"), pj.get("testbench", {}).get("metadata")]
        paths += [s.get("path") for s in pj.get("knowledge_sources", [])]
        root = ROOT.resolve()
        outside = [p for p in paths if p and root not in (root / p).resolve().parents
                   and (root / p).resolve() != root]
        if outside:
            return self.error(400, "paths must stay inside the project folder: " + ", ".join(outside))
        required = paths[:3] + [s.get("path") for s in pj.get("knowledge_sources", [])]
        missing = [p for p in required if not p or not (root / p).exists()]
        if pj.get("testbench", {}).get("metadata") and not (root / pj["testbench"]["metadata"]).exists():
            missing.append(pj["testbench"]["metadata"])
        if not pj.get("design", {}).get("top"):
            missing.append("design.top")
        if missing:
            return self.error(400, "these paths do not exist: " + ", ".join(map(str, missing)))
        tb = (root / pj["testbench"]["file"]).read_text()
        if "@agent-tests" not in tb or "@agent-dispatch" not in tb:
            return self.error(400, "the testbench needs the @agent-tests and @agent-dispatch marker comments")
        tmp = settings.PROJECT_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(pj, indent=1, ensure_ascii=False) + "\n")
        tmp.replace(settings.PROJECT_FILE)
        meta = knowledge.reindex()
        return self.send_json({"ok": True, "reindexed": bool(meta), "meta": meta})

    def resume(self, rid: str, d: Path, b: dict):
        if active_run():
            return self.error(409, f"run {active_run()} is still in progress")
        m = json.loads((d / "manifest.json").read_text())
        if m.get("current_stage") != "failed":
            return self.error(409, "only a run that was escalated to a person can be resumed")
        diff = b.get("diff") or ""
        if "@@" not in diff:
            return self.error(400, "paste a unified diff (lines starting ---, +++, @@)")
        f = d / f"human_fix_{datetime.now().strftime('%H%M%S')}.diff"
        f.write_text(diff if diff.endswith("\n") else diff + "\n")
        p = subprocess.run(["patch", "--dry-run", "-s", str(ROOT / m["working_rtl"])],
                           input=f.read_text(), capture_output=True, text=True)
        if p.returncode:
            f.unlink()
            return self.error(400, "the diff does not apply to this run's RTL: "
                              + (p.stdout + p.stderr).strip()[:300])
        args = ["--resume", rid, "--human-fix", str(f), "--web"]
        if b.get("mode") == "auto":
            args.append("--auto-approve")
        start_process(rid, args, {})
        return self.send_json({"ok": True})


def main() -> int:
    log.setup()
    settings.load_env()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8080"))
    if host not in LOOPBACK and not access_token():
        _log.error("refusing to listen on %s without ASIC_AGENT_TOKEN: the app starts runs "
                   "and edits the project. Set a long random token, or keep HOST=127.0.0.1.", host)
        return 2
    srv = ThreadingHTTPServer((host, port), Handler)
    _log.info("Flow Console on http://%s:%d%s  (Ctrl-C to stop)", host, port,
              "  — sign in once with /?token=…" if access_token() else "")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        with _lock:
            for p in _active.values():
                if p.poll() is None:
                    os.killpg(p.pid, signal.SIGTERM)
    return 0

