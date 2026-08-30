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
      produces a 3-year, 300-client commercial/institutional dataset:
      entity groups, clients, accounts, product holdings (facilities),
      internal risk ratings, transactions, EOD balances, and CRM/campaign
      interactions — with embedded, labeled trigger events held out for
      evaluation. Schema is modeled on real academic/industry references
      (Berka/PKDD'99 financial dataset, Lending Club, UCI German Credit,
      ISO 20022, IBAN/LEI checksums, Eurostat NACE) — see the data
      dictionary for the full mapping.
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

Build the rule-based trigger detection engine against `transactions.csv`
(and now `facilities.csv` / `balances.csv`), then score it against
`trigger_events.csv`.
