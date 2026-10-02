# Deployment

The Flow Console is a single-user engineering tool: one server process, one
run at a time (runs are CPU-heavy and every tool invocation waits for a
person's approval). This page covers running it for a team on a shared host.

## Configuration per environment

Nothing environment-specific lives in the code. Per host:

| Setting | Where | Default |
|---|---|---|
| API keys, `ASIC_AGENT_TOKEN` | `~/.config/asic-agent/secrets.env` (mode 600) or the service environment | — |
| Provider / model per role | `config/llm.toml`, or `LLM_PROVIDER`, `LLM_MODEL`, `EMBEDDING_*` | Groq `qwen/qwen3.8-27b` |
| Active design | `config/project.json` (or `ASIC_AGENT_PROJECT=/path/project.json`) | apb_gpio |
| Listen address | `HOST`, `PORT` | `127.0.0.1:8080` |
| Knowledge service port | `RAG_PORT` (always bound to 127.0.0.1) | 8090 |
| Run outputs / index | `ASIC_AGENT_RUNS`, `ASIC_AGENT_INDEX` | `runs/`, `data/index/` |
| Toolchain locations | `OSS_CAD_SUITE`, `PDK_ROOT`, `BUILD_DIR`, `ORFS_IMAGE` | see `scripts/env.sh` |
| Log level | `ASIC_AGENT_LOG_LEVEL` | `INFO` |

## REST API

Other systems drive the pipeline over `/api/v1` (token auth): see `API.md`.

## Access control

- The app has no user accounts. On `127.0.0.1` only local users can reach it.
- Any other `HOST` **requires** `ASIC_AGENT_TOKEN`; the server refuses to start
  without one. Users sign in once with `https://host/?token=<token>`, which
  sets an HttpOnly, SameSite=Strict cookie. API clients send
  `Authorization: Bearer <token>`.
- Put it behind a TLS-terminating reverse proxy (nginx, Caddy) — the token
  must not travel over plain HTTP. Proxy `/` to `127.0.0.1:8080`.
- Cross-site requests are refused (POSTs must be same-origin JSON); responses
  carry a strict Content-Security-Policy and `X-Frame-Options: DENY`.
- API keys never reach the browser; the app accepts only provider/model pairs
  listed in `config/llm.toml` with a key present.

## Running as a service (systemd example)

```ini
# /etc/systemd/system/asic-agent.service
[Unit]
Description=ASIC agent Flow Console
After=network-online.target docker.service

[Service]
User=asic
WorkingDirectory=/srv/asic-agent
Environment=HOST=127.0.0.1 PORT=8080
EnvironmentFile=/home/asic/.config/asic-agent/secrets.env
ExecStart=/srv/asic-agent/scripts/start_app.sh
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

The user needs Docker access (OpenROAD runs in the pinned ORFS image).

## Monitoring and logs

- `GET /api/health` — `{"ok": true, "active": <run id or null>}`, no token needed.
- Server and knowledge-service logs: stderr (journald under systemd);
  `runs/.console/kb_service.log`.
- Each run: `runs/<id>/manifest.json` (the record, including approvals,
  diagnoses, model switches, internal errors) and `runs/.console/<id>.log`.
- A run that hits a model's daily quota ends as `quota_stopped` (no attempt
  spent); an unexpected exception ends as `error` with the traceback in its
  console log; a stop from the app ends as `stopped`.

## Backups

- `data/knowledge/known_failure_patterns.jsonl` — the confirmed-fix record the
  agents learn from. It is versioned in git; commit it after runs that confirm
  fixes, or back it up.
- `runs/` — run evidence, if you need to keep it (large: netlists, layouts).
  The index (`data/index/`) and exported patterns are rebuilt on demand.

## Upgrading

```bash
git pull
python3 -m asic_agent check
sudo systemctl restart asic-agent
```

Before switching models, check the new one with
`python3 -m asic_agent models test PROVIDER MODEL`, then try one change request
on a known case before relying on it.
After changing the embedding model, the index rebuilds itself on the next
query (or `python3 -m asic_agent kb-reindex`).

## Limits

- One run at a time per server (by design; see above).
- Model quotas are per provider and model; size the plan for the expected
  number of requests (a change request uses roughly 20–60k tokens).
