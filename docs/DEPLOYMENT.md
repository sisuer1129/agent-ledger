# Deployment guidance

Agent Ledger is a self-hosted application. Deploy it only where the operator controls the API key and persistent database directory.

## Fresh-environment setup

Run these commands from the repository root. Python 3.9.6 was verified for this release. Other Python versions are not separately validated. Node.js is needed for the frontend checks:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
export FINANCE_API_PORT=5009
export FINANCE_DB_PATH="$PWD/runtime/agent-ledger.db"
export FINANCE_API_KEY='replace-this-with-a-long-random-secret'
.venv/bin/python release/app/main.py
```

Open `http://127.0.0.1:5009` from a trusted browser and enter the same API key in Settings. Before exposing the service beyond a trusted network, place it behind access controls appropriate to the environment.

## Required configuration

- `FINANCE_API_KEY` must be a strong, unique random value. The application refuses to start when it is absent.
- `FINANCE_DB_PATH` should point to a writable persistent location. The project-relative default is `runtime/agent-ledger.db`.
- `FINANCE_API_PORT` is optional and defaults to `5009`.

Do not use a database path inside a disposable container layer or temporary directory for a long-running service. For a Docker, NAS, or VPS deployment, mount or otherwise provide a persistent directory and set `FINANCE_DB_PATH` inside that persistent location. Use only environment-specific placeholders in deployment tooling; do not publish actual host paths, container names, IP addresses, domains, credentials, or backups.

## Data persistence and backups

- Persist the directory containing the SQLite database.
- Back up the database to a separate, access-controlled location on a regular schedule.
- Test recovery on a copy before relying on a backup procedure.
- Before any upgrade, migration, schema change, or application/image replacement, back up both the SQLite database and the deployment configuration in a private, access-controlled location.
- Verify that the backup can be restored before proceeding, and never place `.env` or its secret values in Git.
- Keep databases, transaction exports, backup files, runtime logs, and `.env` files out of the repository and out of public issue attachments.

## Verification after deployment

Use a temporary API key only for local smoke testing. For an installed instance, verify that `/health` returns `{"status":"ok"}`, then call `/taxonomy` with the `X-API-Key` header. Run the repository checks before every release:

```bash
PYTHON_BIN=.venv/bin/python bash release/scripts/verify-release.sh
```

There is no bundled Docker image, NAS package, VPS configuration, or Hermes deployment requirement. Hermes is an optional reference for external agent integrations only.
