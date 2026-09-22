# Vector Bridge

A small FastAPI service that lets a desk robot answer questions about the homelab out loud, and run a fixed set of pre-approved operations.

Full write-up: [../../docs/monitoring.md](../../docs/monitoring.md)

## Commands

**Read-only** — answered immediately:

| Phrase | Answer |
|---|---|
| `server status` / `status` | How many monitored services are up or down |
| `disk space` | Free space and percentage |
| `uptime` | How long the host has been up |
| `load` | One-minute load average |

**Write** — only phrases defined in `ops.yaml`, each submitted to an approval gate before anything runs.

Anything else gets "I don't know that one."

## Running it

```bash
python3 -m venv venv
./venv/bin/pip install fastapi uvicorn requests pyyaml
cp .env.example .env        # fill in the token
cp ops.example.yaml ops.yaml
./venv/bin/uvicorn app:app --host 0.0.0.0 --port 8099
```

Under systemd, with the environment file loaded by the unit:

```ini
[Unit]
Description=Vector Bridge API
After=network-online.target

[Service]
WorkingDirectory=/opt/vector-bridge
EnvironmentFile=/opt/vector-bridge/.env
ExecStart=/opt/vector-bridge/venv/bin/uvicorn app:app --host 0.0.0.0 --port 8099
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

## Security notes

- The token is required at import time — the service refuses to start without one, rather than running unauthenticated.
- Bind it to an interface reachable only from your private network. There is no rate limiting yet, so it should not be exposed publicly.
- `.env` and `ops.yaml` are gitignored. Only the examples are committed.
