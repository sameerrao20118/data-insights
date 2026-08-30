# Next Best Action / Event-Based Marketing POC — Commercial & Institutional Banking

A proof-of-concept pipeline for detecting client "trigger events" in
commercial/institutional banking transaction data (e.g. a large tender
payment, emerging FX exposure, treasury cash buildup) and turning them into
ranked, human-readable relationship-manager recommendations — the pattern
banks like BNP Paribas, ING, Santander, and Nordea call Next Best Action
(NBA) or Event-Based Marketing (EBM).

No real client data is used anywhere in this repo. See
[`data_generator/output/data_dictionary.md`](data_generator/output/data_dictionary.md)
for why, and how the synthetic data is grounded in public statistics instead.

## Status

See [`docs/current_state.md`](docs/current_state.md) for the full, current
picture — what's verified working, what's NOT RUN, and known limitations.
Short version:

- [x] Synthetic data generator (dev + independently-seeded holdout dataset)
- [x] Typed config profiles (`offline_ollama` active; `snowflake_trial_ollama`
      wired but NOT RUN — no credentials configured)
- [x] Offline `DataSource` adapter (DuckDB), tested against leakage
- [x] Detector: large-incoming-payment vs. trailing baseline (9/9 tests pass)
- [x] Explicit ranking formula, deterministic + local-Ollama narrative,
      offline-sampled judge, SQLite state, local RM digest — all run
      end-to-end, no paid calls, no Snowflake/AWS calls
- [x] Evaluation harness (dev-diagnostic only — see contamination
      disclosure in `data_generator/output/protected_evaluator_only/README.md`)
- [ ] Replay/monitor mode with checkpoints; ML ranking challenger; AWS path

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run the full pipeline (local Ollama, no credentials needed)

```bash
source .venv/bin/activate
python -m datainsights.cli                  # detect -> rank -> narrate -> digest
python -m datainsights.status                # what happened last
python -m datainsights.judge.run_sample 10   # offline semantic judge on a sample
python -m evaluation.evaluate                # dev-diagnostic precision/recall
python -m pytest tests/ -v                   # detector test suite
```

## Generate the dataset

```bash
source .venv/bin/activate
python data_generator/generate_data.py
```

Writes to `data_generator/output/`:

- `entity_groups.csv` — corporate/institutional group hierarchy (2-tier: ultimate parent / intermediate holding / subsidiary)
- `clients.csv` — 300 synthetic commercial/institutional clients (LEI, NACE 4-digit, group membership, PEP/sanctions-screening fields)
- `accounts.csv` — client accounts with structurally valid IBAN/BIC, incl. realistic closures
- `facilities.csv` — product holdings (loans, credit lines, trade finance, guarantees), causally linked to utilization-spike triggers
- `risk_ratings.csv` — annual internal credit rating per client
- `transactions.csv` — ~303k transactions, 2023-01-01 to 2025-12-31, booking/value date, ISO 20022 purpose codes + message-type tags, bank fees, counterparty jurisdiction risk tagging
- `balances.csv` — end-of-day balance snapshots, primary accounts
- `crm_interactions.csv` — RM/campaign engagement log (source of future ML training labels)
- `trigger_events.csv` — **ground truth** trigger labels, held separate from
  everything else so you can measure precision/recall of any detector built
  on top of it without peeking at the answer key
- `data_dictionary.md` — full schema, trigger-type rationale, and the
  real academic/industry references each part of the schema is modeled on

## Next step

Replay/monitor mode with real checkpoints (project instructions §10.1),
then a coding-process benchmark (§9). Wire Snowflake yourself when ready
(`config/profiles/snowflake_trial_ollama.yaml` documents the env vars) —
the detector code won't need to change.
