# Sickbay

A self-hosted kidney stone history tracker for a patient and a caregiver.

People with recurring kidney stones are asked the same questions at every new urologist, ER visit and follow-up: how
many stones, how often, how big, what were they made of, what was done, were there infections, how long in hospital.
Sickbay is one private place that holds the whole history: one record per stone episode (an event), filled in during
and after it, with the patient portal's documents attached. It prints a one-page summary for a doctor and shows the
pattern over the years, above all how long it usually is between stones.

**It records history. It gives no medical advice**: no recommendation, risk score or predicted date, anywhere. Medians
and counts of what happened are shown; nothing is projected forward.

Two users sign in through Cloudflare Access; every other request is refused. The design is in
`docs/superpowers/specs/2026-11-14-sickbay-design.md`.

## Development

Python 3.12 or later.

```sh
python3 -m venv .venv && . .venv/bin/activate
python -m pip install -r requirements-dev.txt
DEV_EMAIL=alex@example.com python3 app.py
```

The app listens on `http://127.0.0.1:8774` and keeps its database and files in `./data` (git-ignored). `DEV_EMAIL`
admits requests from this machine as that user, and only while no Cloudflare header is present; never set it on a
server. The other settings are listed at the top of `settings.py` and in `.env.example`.

Before every commit:

```sh
python -m pytest -q && ruff check && python .github/scripts/privacy_scan.py
```

## Deploying
