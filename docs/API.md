# REST API

Other systems can drive the pipeline over HTTP: submit a change request or a
verification run, answer its approval and decision gates, and read the
results. The browser app uses the same API.

- Base path: `/api/v1` (the full specification is served at `/api/v1/openapi.json`
  and lives in `src/asic_agent/web/openapi.json`).
- Authentication: `Authorization: Bearer <ASIC_AGENT_TOKEN>` when the server has
  a token (always when it listens beyond localhost; see `DEPLOYMENT.md`).
- Requests with a body must be `Content-Type: application/json`; cross-site
  browser requests are refused.
- One run at a time per server: a second start returns `409`.

## Lifecycle of a run

```
POST /requests  ─► run_id
loop:
  GET /runs/{run_id}
    gate != null  → POST /runs/{run_id}/gate   (approve a tool run / choose an option)
    running false → finished: result is done | failed | quota_stopped | error | stopped
```

Every Verilator, Yosys and OpenROAD run, and every decision that changes what
the design should do, waits at a gate. With `"mode": "auto"` the server answers
each gate (tool runs approved, decisions take the recommended option) and records it; with `"manual"` your system (or a
person in the browser) answers. Answers are recorded with the `by` you send.

| `result` | Meaning |
|---|---|
| `done` | Implemented, verified at RTL and gate level, signed off |
| `failed` | Needs a person (attempt cap reached, or a decision declined); `POST /runs/{id}/resume` with a diff continues it |
| `quota_stopped` | The model's quota ran out; nothing was spent — start it again later or with another model |
| `error` | An internal error ended it; the console log has the traceback |
| `stopped` | Stopped on request |

## Example (Python, standard library only)

```python
import json, os, time, urllib.request

BASE = "http://127.0.0.1:8080/api/v1"
TOKEN = os.environ.get("ASIC_AGENT_TOKEN", "")

def call(path, body=None):
    req = urllib.request.Request(BASE + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": f"Bearer {TOKEN}"} if TOKEN else {})})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

run = call("/requests", {
    "request": "The interrupt output must stay asserted as a level until software reads INTSTATUS.",
    "mode": "manual",                               # we answer the gates below
    "provider": "groq", "model": "qwen/qwen3.8-27b" # optional; default from config/llm.toml
})["run_id"]

while True:
    r = call(f"/runs/{run}")
    g = r.get("gate")
    if g:
        if g["kind"] == "approve":                  # a tool run; g["action"] says which
            call(f"/runs/{run}/gate", {"id": g["id"], "approve": True, "by": "ci-bot"})
        else:                                       # a decision: options are in g["options"]
            print(g["title"], g["options"])         # route to a person, or take the recommendation
            call(f"/runs/{run}/gate", {"id": g["id"], "choice": g["recommended"], "by": "ci-bot"})
    elif not r["running"] and r["result"] in ("done", "failed", "quota_stopped", "error", "stopped"):
        break
    time.sleep(3)

print(r["result"], r["signoff"].get("setup_wns_ns"), r["signoff"].get("lec_equivalent"))
print(r["final_diff"])                              # the RTL change
```

## Useful endpoints

| Method and path | Purpose |
|---|---|
| `GET /health` | liveness (no token) |
| `GET /runs`, `GET /runs/{id}` | run list; full record (stages, tests, edits, signoff, history, open gate) |
| `GET /runs/{id}/console?offset=N` | live console text, incremental |
| `GET /runs/{id}/file?path=rtl_final.diff` | artifacts inside the run folder |
| `POST /requests`, `POST /runs` | start a change request / a verification run |
| `POST /runs/{id}/gate`, `/resume`, `/stop` | answer a gate, continue with a person's fix, stop |
| `GET /models`, `POST /models/test` | providers and key status (never the key); test a model |
| `GET /project`, `POST /project` | the active design; replacing it rebuilds the knowledge base |
| `GET /knowledge/search?q=…`, `GET /knowledge/record` | search the knowledge base; the confirmed-fix record |
