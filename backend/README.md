# SUDHARSARTHI Backend

Flask REST API and storage layer for the SudharSarthi civic issue reporting platform.

## Architecture

| Module | Purpose |
|---|---|
| `database.py` | SQLite persistence (`sudharsarthi.db`) with auto-migration. Handles `reports`, `users`, and `sessions` tables. |
| `duhai.py` | Configurable service boundary for the Ghaziabad & Duhai region (~26 km × 26 km). Overridable via environment variables. |
| `urgency_ai.py` | Explainable AI urgency engine. Claude 3.5 Sonnet vision check + heuristic rules engine (0-100 severity, traffic volume, repeat reports, aging factor). |
| `notifier.py` | Ward officer notification system. Sends real SMTP emails or writes outbox files to `backend/outbox/`. |
| `app.py` | REST API handling citizen authentication, report submission, status workflows, and analytics. |

## Running the Backend

```bash
# In backend/ directory:
pip install -r requirements.txt
python test_api.py      # Run test suite
python app.py           # Starts server on http://localhost:5000
```

## Running Automated Tests

```bash
python test_api.py
```
Executes 16 tests covering health, config, registration, login, logout, session expiration, citizen report isolation, map boundary enforcement, photo uploads, urgency classification, and budget AI removal verification.
