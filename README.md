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

- [x] **Synthetic data generator** — `data_generator/generate_data.py`
      produces a 3-year, 300-client commercial/institutional transaction
      dataset with embedded, labeled trigger events for later evaluation.
- [ ] Rule-based trigger detection engine
- [ ] ML opportunity scoring/ranking model
- [ ] Excel/email digest output
- [ ] LLM narrative layer + agentic orchestration

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Generate the dataset

```bash
source .venv/bin/activate
python data_generator/generate_data.py
```

Writes to `data_generator/output/`:

- `clients.csv` — 300 synthetic commercial/institutional clients
- `accounts.csv` — client accounts (current, savings, credit facility)
- `transactions.csv` — ~297k transactions, 2023-01-01 to 2025-12-31
- `trigger_events.csv` — **ground truth** trigger labels, held separate from
  transactions.csv so you can measure precision/recall of any detector built
  on top of it without peeking at the answer key
- `data_dictionary.md` — full schema + trigger-type rationale

## Next step

Build the rule-based trigger detection engine against `transactions.csv`,
then score it against `trigger_events.csv`.
