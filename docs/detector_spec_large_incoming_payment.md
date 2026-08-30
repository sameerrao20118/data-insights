# Detector spec: Large Incoming Payment vs. Client's Own Trailing Baseline

Status: **spec only, not yet implemented.** Written before code per project
instructions §5.

## Business hypothesis and limits of interpretation

A credit transaction materially larger than a client's own recent typical
inflow *may* indicate a one-off event worth an RM's attention — a large
contract/tender payment, an asset sale, a grant, a large customer
settlement, etc. It is a **statistical anomaly signal only**. It does not,
by itself, establish:
- that the payment relates to a tender or contract award,
- the client's net cash position or investable surplus (this is one
  transaction, not a balance-level view — no balances.csv correlation
  attempted in v1 of this detector),
- creditworthiness, intent, or product suitability.

Any narrative generated downstream must describe this as "unusually large
incoming payment relative to this client's recent pattern," not as a
specific business event, unless further evidence field exist to support a
narrower claim.

## Why this rule, not one tuned to known injections

This session authored the synthetic data's trigger-injection logic and has
seen `trigger_events.csv` (see the contamination disclosure in
`data_generator/output/protected_evaluator_only/README.md`). To avoid
reverse-engineering a rule from that knowledge, this spec is written from
generic statistical reasoning about payment data (a robust deviation from a
client's own trailing distribution), not from the known injection
amount/window parameters. Any resemblance to the injected `TENDER_PAYMENT`
pattern is expected — a well-designed generic rule and a well-designed
injection should overlap — but this rule must be independently defensible
without reference to the generator.

## Required sources and field semantics

Per `config/entities.yaml`: `transactions` only (`booking_date`, `amount`,
`direction`, `currency`, `account_id`, `client_id`). No other table is
required for v1.

## Entity grain

One evaluation per **(client_id, account_id, currency)** — never mixed
across currencies (see entities.yaml `cross_currency_aggregation:
forbidden`; no FX rate source is configured). In this dataset every client
has one primary current account, so this is effectively per-client-per-
currency, but the rule is defined at account+currency grain in case a
client ever has more than one relevant account.

## Event time vs. detection time

- **Event time** = `booking_date` of the flagged transaction.
- **Earliest possible detection time** = same as event time in this
  dataset, because no separate ingestion/availability timestamp exists
  (see entities.yaml note). This is event-time replay, not true
  availability-aware backtesting — must be stated wherever "as of" language
  appears in output, per project instructions.

## Baseline window, minimum history, cold start

- Baseline = trailing 90 calendar days of **prior** credit transactions on
  the same (account_id, currency), strictly excluding the transaction being
  evaluated and anything on or after its booking_date (no future leakage).
- Minimum history: at least 15 prior credit transactions in that trailing
  window. Below that, status = `insufficient_evidence`, not a negative
  detection.
- Cold start (client onboarded < 90 days before the candidate transaction):
  use whatever trailing window exists back to `onboarding_date`; the
  15-transaction minimum still applies, so a very new client is likely
  `insufficient_evidence` for a while — this is intentional, not a gap to
  patch by lowering the minimum.

## Threshold (provisional — not yet validated)

Flag when: `amount > median(baseline) + 6 * MAD(baseline)` **and**
`amount > 5000` in the transaction's currency (a floor so a client with a
tiny, tight baseline doesn't get flagged over noise-level amounts).

MAD (median absolute deviation) is used instead of mean/stddev because
transaction amounts are heavy-tailed/right-skewed; MAD is far less
sensitive to the very outliers we're trying to detect distorting the
baseline itself. The multiplier (6) and floor (5000) are **provisional
defaults**, not validated against any outcome — they need review against
development metrics (§ below) before being treated as final, and even then
only as development diagnostics per the contamination disclosure, not a
validated business threshold.

## Observation window, exclusions, duplicates, reversals, aggregation

- Only `direction == credit` transactions are candidates.
- No special reversal handling in v1 — this dataset has no reversal/
  storno field in the contract. If real data has one, exclude reversed
  transactions from both the baseline and candidacy; flag as a known gap
  until then.
- No intra-day aggregation — each transaction is evaluated individually,
  not summed with same-day transactions. (A future version might consider
  same-day-multiple-payments-from-one-counterparty as a related pattern;
  out of scope for v1.)

## Trigger identity, repeat suppression, late data, rerun behaviour

- Trigger identity = `(account_id, currency, transaction_id)` — stable
  across reruns on the same snapshot.
- Cooldown: once a (client_id, account_id) pair has an active detection,
  suppress new detections of this same rule for that pair for 30 days,
  to avoid one volatile client generating daily alerts. Configurable.
- Rerunning the detector on an unchanged snapshot/config must not create a
  second active detection for the same transaction_id (idempotent).
- Late-arriving/corrected data: not handled in v1 (no ingestion-timestamp
  field exists to detect lateness in this dataset) — flagged as a known
  gap, not silently ignored.

## Evidence fields to persist

`detection_id, rule_version, client_id, account_id, currency,
transaction_id, event_date (=booking_date), detection_as_of,
flagged_amount, baseline_median, baseline_mad, baseline_n,
threshold_multiplier, threshold_floor, run_id, status
(detected/insufficient_evidence/suppressed_cooldown)`.

## Failure modes / test cases (to write before implementation, per §6)

1. Client with exactly 14 prior transactions (below minimum) → `insufficient_evidence`, not detected.
2. Client with exactly 15 → eligible.
3. Transaction below the floor (e.g. €4,999 for a low-baseline client) → not detected even if statistically extreme.
4. Two flag-worthy transactions same client same day → both flagged individually (no dedup across different transaction_ids), but see cooldown for subsequent days.
5. Rerun on identical snapshot/config → no duplicate active detection.
6. All-debit account (no credit transactions ever) → `insufficient_evidence`, never a false detection from an empty baseline.
7. Multi-currency client → baselines computed separately per currency; a EUR anomaly must not be inferred from USD history.
8. Transaction exactly at threshold boundary → documented rounding/comparison behavior (`>`, not `>=`).

## Acceptance criteria (provisional, pre-tuning)

To be proposed for discussion once implemented and run against development
data — not fixed yet, and any number produced against
`protected_evaluator_only/trigger_events.csv` in `data_generator/output/`
is a **development diagnostic only** (contaminated session), not a
validated benchmark. A legitimate acceptance bar needs the separately
generated holdout (`data_generator/output_holdout/`) evaluated by a party
that doesn't share this session's knowledge of the injection logic — see
that directory's README for the caveat that even this holdout only
partially satisfies that requirement.
