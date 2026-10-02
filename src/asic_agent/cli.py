"""Command-line entry point:  python -m asic_agent <command> [args]

    run         the flow (verification run, change request, or resume) —
                arguments as documented in orchestrator/driver.py
    serve       the Flow Console web app (127.0.0.1:$PORT, default 8080)
    kb-service  the knowledge retrieval service (127.0.0.1:$RAG_PORT, 8090)
    kb-reindex  rebuild the knowledge index in-process (no service needed)
    kb-search   query the index:  kb-search "when is INTSTATUS cleared" [-k 5]
    check       validate configuration, keys (names only), tools and paths
    models      set up and check model providers:
                  models                       providers, keys present, roles
                  models add-key PROVIDER      store a key (typed, not echoed)
                  models available PROVIDER    models the provider offers this key
                  models test PROVIDER MODEL [--embeddings]
                  models use ROLE PROVIDER MODEL   ROLE: chat | fallback | embeddings

Shell wrappers in scripts/ source scripts/env.sh (toolchain, PDK) first.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

from . import log, settings


def _check() -> int:
    from .llm import LLMConfigError, load_config

    ok = True

    def line(good: bool, what: str, detail: str = "") -> None:
        nonlocal ok
        ok &= good
        print(f"  [{'ok' if good else '!!'}] {what}" + (f" — {detail}" if detail else ""))

    print("Configuration")
    used = settings.load_env()
    line(True, "secrets files", ", ".join(str(p) for p in used) or "none (environment only)")
    try:
        cfg = load_config()
        line(True, "config/llm.toml", f"{len(cfg.providers)} providers")
        if cfg.chat:
            line(cfg.chat.provider.has_key(), f"chat: {cfg.chat.provider.name} / {cfg.chat.model}",
                 f"key in ${cfg.chat.provider.api_key_env}" if cfg.chat.provider.has_key()
                 else f"${cfg.chat.provider.api_key_env} is not set")
        else:
            line(False, "chat", "no provider configured: agents disabled")
        if cfg.fallback:
            line(True, f"fallback: {cfg.fallback.provider.name} / {cfg.fallback.model}",
                 "key present" if cfg.fallback.provider.has_key() else "no key: fallback unused")
        if cfg.embeddings:
            line(cfg.embeddings.provider.has_key(),
                 f"embeddings: {cfg.embeddings.provider.name} / {cfg.embeddings.model}")
        else:
            print("  [--] embeddings: none configured (keyword-only retrieval)")
        keys = [p.api_key_env for p in cfg.providers.values() if p.has_key()]
        print(f"       keys present: {', '.join(keys) or 'none'}")
    except LLMConfigError as e:
        line(False, "config/llm.toml", str(e))

    print("Project")
    try:
        p = settings.load_project()
        line(True, "config/project.json", p.get("name", "?"))
        for what, rel in [("RTL", p["design"]["rtl"]), ("spec", p["design"]["spec"]),
                          ("testbench", p["testbench"]["file"])]:
            line(settings.project_path(rel).exists(), what, rel)
        for s in p.get("knowledge_sources", []):
            line(settings.project_path(s["path"]).exists(), f"knowledge source ({s.get('kind')})", s["path"])
    except (OSError, ValueError, KeyError) as e:
        line(False, "config/project.json", f"{type(e).__name__}: {e}")

    print("Tools (after `source scripts/env.sh`)")
    for tool in ("verilator", "yosys", "docker", "patch"):
        line(shutil.which(tool) is not None, tool, shutil.which(tool) or "not on PATH")
    for var in ("LIB_TT", "BUILD_DIR", "ORFS_IMAGE", "TOP"):
        line(bool(os.environ.get(var)), f"${var}", "set" if os.environ.get(var) else "not set")

    print("Knowledge index")
    idx = settings.INDEX_DIR / "index" / "meta.json"
    line(idx.exists(), str(idx.relative_to(settings.ROOT)),
         "built" if idx.exists() else "not built: python -m asic_agent kb-reindex")
    print("\nAll checks passed." if ok else "\nSome checks failed (see [!!] above).")
    return 0 if ok else 1


def _models(rest: list[str]) -> int:
    from .llm import LLMConfigError, LLMError, manage

    try:
        if not rest:
            for p in manage.status():
                roles = ", ".join(f"{r}: {m}" for r, m in p["roles"].items())
                print(f"{p['provider']:12} {'key ok ' if p['has_key'] else 'no key '} "
                      f"${p['key_env']:20} {roles}")
            print("\nadd a key:  python3 -m asic_agent models add-key PROVIDER")
            return 0
        sub, args = rest[0], rest[1:]
        if sub == "add-key" and len(args) == 1:
            import getpass
            key = getpass.getpass(f"API key for {args[0]} (not shown): ")
            path = manage.save_key(args[0], key)
            print(f"saved to {path} (mode 600). Test it: python3 -m asic_agent models test {args[0]} MODEL")
            return 0
        if sub == "available" and len(args) == 1:
            print("\n".join(manage.available_models(args[0])))
            return 0
        if sub == "test" and len(args) in (2, 3):
            r = manage.test(args[0], args[1], embeddings="--embeddings" in args[2:])
            print(("OK  " if r["ok"] else f"FAILED ({r['error']})  ")
                  + (f"{r['latency_ms']} ms  " if r.get("latency_ms") else "") + r["detail"])
            return 0 if r["ok"] else 1
        if sub == "use" and len(args) == 3:
            manage.set_role(args[0], args[1], args[2])
            print(f"{args[0]} -> {args[1]} / {args[2]}  (config/llm.toml updated)")
            if args[0] == "embeddings":
                print("the knowledge index rebuilds with the new embeddings on its next use")
            return 0
    except (LLMConfigError, LLMError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(__doc__, file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    log.setup()
    settings.load_env()
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]

    if cmd == "run":
        from .orchestrator import driver
        sys.argv = ["asic_agent run", *rest]
        return driver.main()
    if cmd == "serve":
        from .web import server
        return server.main()
    if cmd == "kb-service":
        from .knowledge import service
        return service.main()
    if cmd == "kb-reindex":
        from .knowledge import client, service
        if client.available():          # the running service must reload, not just the files
            meta = client.reindex()
            where = "by the running service"
        else:
            meta = service.reindex()
            where = "in-process (no service running)"
        print(f"index rebuilt {where}: {meta['n_chunks']} chunks ({meta['mode']})")
        return 0
    if cmd == "kb-search":
        from .knowledge.retriever import Retriever
        ap = argparse.ArgumentParser(prog="asic_agent kb-search")
        ap.add_argument("query")
        ap.add_argument("-k", type=int, default=5)
        a = ap.parse_args(rest)
        r = Retriever.load()
        print(f"[{r.mode}]")
        for h in r.retrieve(a.query, k=a.k):
            print(f"{h.score:.3f}  {h.kind:9} {h.location()}  {h.label}")
        return 0
    if cmd == "check":
        return _check()
    if cmd == "models":
        return _models(rest)
    print(f"unknown command '{cmd}'\n{__doc__}", file=sys.stderr)
    return 2
