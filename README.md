# Agent Ledger

Agent Ledger is a self-hosted personal finance ledger designed for AI-agent-driven bookkeeping. It combines a Flask and SQLite API with a static web interface, while keeping the operator in control of the local database and API key.

> **Early-stage project.** Review each transaction before relying on it. This project is not a bank, does not promise bank-grade security or perfect automatic classification, is not intended for enterprise accounting, and has not been validated through large-scale production use.

## Core features

- Accounts, transactions, balances, budgets, categories, tags, and CSV export.
- Credit-card billing cycles, repayment entries, and budget-period views.
- Deterministic classification rules and editable category/tag seeds.
- A small authenticated JSON API for local automation and AI-agent workflows.

## Web interface

Open the web interface to enter or correct transactions, review suggested classifications, manage accounts and budgets, inspect summaries, and export a ledger. The browser stores the API address and API key only in its local browser storage; it does not receive a preset remote endpoint or key from this repository.

## AI Agent integration

An agent should call the authenticated local API rather than read or write the SQLite file directly. Give the agent the minimum access it needs, use a dedicated local API key, and require human review for ambiguous or high-value transactions.

For example, an agent can first obtain the current taxonomy:

```bash
curl -sS \
  -H "X-API-Key: $FINANCE_API_KEY" \
  http://127.0.0.1:5009/taxonomy
```

It can then create transactions through `POST /transactions` using the category, tag, and account IDs returned by the API. See the request validation in `release/app/routes.py` and the API tests in `tests/` for supported fields and examples.

Hermes may be used as a reference integration for an external agent workflow, but it is not required to run Agent Ledger. This repository contains no Hermes configuration, credentials, deployment record, or private workflow.

## Local run

Requirements: Python 3.9.6 was verified for this release. Other Python versions are not separately validated. Node.js (for frontend tests), and a shell with `curl` for the example above are also required.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
export FINANCE_API_PORT=5009
export FINANCE_DB_PATH="$PWD/runtime/agent-ledger.db"
export FINANCE_API_KEY='replace-this-with-a-long-random-secret'
.venv/bin/python release/app/main.py
```

Open `http://127.0.0.1:5009`, then enter the same API key in **Settings**. The application creates its SQLite database at `runtime/agent-ledger.db` by default.

## Environment variables

| Variable | Required | Meaning |
| --- | --- | --- |
| `FINANCE_API_KEY` | Yes | A long, random secret required by authenticated API endpoints. |
| `FINANCE_DB_PATH` | Yes for explicit deployments | SQLite database path. Defaults to `runtime/agent-ledger.db` relative to the project. |
| `FINANCE_API_PORT` | No | HTTP port; defaults to `5009`. |

The committed `.env.example` is only a template. Do not commit a real `.env` file.

## Tests and checks

```bash
PYTHON_BIN=.venv/bin/python bash release/scripts/verify-release.sh
```

This runs the full Python test suite, Node frontend tests, Python compilation checks, and a frontend static check.

## Data and backups

`runtime/` is the default local data directory and is ignored by Git. Keep it on persistent storage in any long-running deployment. Back up the SQLite database regularly to a separate, access-controlled location; test restoration before relying on a backup. Never commit, publish, or attach a database, transaction export, backup, screenshot, or runtime log to an issue.

Before any upgrade, migration, schema change, or application/image replacement, back up both the SQLite database and the deployment configuration in a private, access-controlled location. Verify that the backup can be restored before proceeding, and never place `.env` or its secret values in Git.

## Privacy and security

Agent Ledger is designed to keep financial records local, but that does not remove operational risk. Protect the API key, limit network exposure, keep the database directory private, and review agent-created transactions. See [SECURITY.md](SECURITY.md) and [deployment guidance](docs/DEPLOYMENT.md).

## Current limitations

- Automatic classification is rule-based assistance, not a guarantee of correctness.
- SQLite is suitable for a single trusted operator or modest local use; it is not a multi-tenant accounting service.
- There is no bundled cloud deployment, Docker image, bank synchronization, or Hermes dependency.
- This project is in an early stage; treat exports, migrations, and agent automation with care.
