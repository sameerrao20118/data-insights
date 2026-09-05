# Industry precedent and compliance boundaries for the exogenous event category

Checked live against current sources (web search, 2026-09-05) rather than
asserted from memory, per this project's own documentation-verification
standard. Two questions: (1) is anyone doing this in the open already,
(2) does GDPR/EU AI Act constrain what this system's outputs can honestly
claim or do.

## 1. Industry precedent — is this already an open-source pattern?

**No direct precedent found.** Searched for macro/geopolitical-event-driven
Next Best Action systems in commercial banking, open source or published.
What exists instead:

- Geopolitical risk is well-established in banking literature, but framed
  as **systemic risk to the bank itself** — the ECB's Macroprudential
  Bulletin and a 2025 European Parliament study both treat geopolitical
  shocks as a macro-financial stability concern (asset price corrections,
  credit risk, funding stress), not as a per-client opportunity signal.
  ([ECB, Apr 2025](https://www.ecb.europa.eu/press/financial-stability-publications/macroprudential-bulletin/html/ecb.mpbu202504_01~6aa0c34852.en.html);
  [European Parliament, 2025](https://www.europarl.europa.eu/RegData/etudes/IDAN/2025/773718/ECTI_IDA(2025)773718_EN.pdf))
- The "commercial NBA engines scan CRM/transactions for treasury/borrowing
  needs" pattern (Backbase, BCG — already cited earlier in this project)
  is real and published, but describes **endogenous** triggers only
  (a client's own data). Nobody in the sources checked connects that to
  exogenous market/political events the way `external_events/` does.

**Honest conclusion**: the exogenous half of this project is a genuine
extension, not a reproduction of an established pattern. That's not a
red flag — it just means there's no external benchmark to validate this
design against, so it should be held to its own stated caveats (§
"What this deliberately does NOT claim" in `external_events/README.md`)
more strictly, not less, until real outcome data exists.

## 2. GDPR — does this system process personal data the way it's built now?

**Mostly no, with two boundary cases worth watching.** GDPR governs
personal data of identifiable natural persons. This system's
sector/country matching operates on **legal entities** (`clients.csv`:
segment, sector, country, revenue) — a company's registered country is
not, by itself, personal data about a natural person.

Two places where that stops being true:

- **Sole traders.** A sole proprietorship is legally a natural person
  conducting business. If any real "SME" client were a sole trader (this
  synthetic dataset doesn't model that distinction — every client is
  generated as a corporate entity with a `legal_name`), GDPR would apply
  to them directly, and the sector/country matching would need the same
  legal basis analysis as any consumer profiling.
- **Named individuals already in the schema.** `clients.csv` carries a
  `pep_flag` (politically-exposed-person indicator) tied to an entity's
  officers. PEP status is adjacent to Article 9 special-category data
  (political exposure) when linked to an identifiable person. The
  exogenous detector currently **does not read `pep_flag` at all** — it
  matches on sector/country only — and that's a boundary worth keeping,
  not crossing casually if a future detector wants to combine "political
  event" signals with "politically exposed person" flags. That specific
  combination is exactly the kind of correlation that turns a defensible
  sector/country match into a much harder-to-justify one.

**Legitimate interest is not a rubber stamp.** The EDPB's October 2024
draft guidelines on legitimate interest (still the operative guidance
checked today) are explicit that profiling for marketing purposes needs a
documented Legitimate Interest Assessment — necessity, balancing test,
and a working objection mechanism — not just an assumption that "it's for
business purposes so it's fine."
([EDPB guidelines summary, Lexology](https://www.lexology.com/library/detail.aspx?g=eb08e746-c245-49a0-9d8b-318221d939d4);
[Morgan Lewis](https://www.morganlewis.com/blogs/sourcingatmorganlewis/2024/10/gdpr-when-can-data-controllers-rely-on-legitimate-interests-for-data-processing-new-guidelines-from-the-edpb))
This project doesn't need to build that assessment — it's a synthetic POC,
nobody is actually contacted — but any move toward a real deployment would
need one before this system's outputs drive real outreach.

## 3. EU AI Act — does the `FINANCING_NEED` tagging count as high-risk credit scoring?

**Checked directly (2026-09-05): no, as currently scoped.** Annex III's
high-risk classification for creditworthiness assessment applies
specifically to AI evaluating **natural persons'** credit scores. The
Digital Omnibus (July 2026) pushed the Annex III enforcement date to
December 2027 regardless, but the scope boundary is the more important
fact here: models used for **institutional counterparty risk or macro
credit signals for internal treasury purposes are explicitly stated as
outside Article 6/Annex III scope**, provided no individual consumer
creditworthiness determination results.
([Openlayer, EU AI Act Credit Scoring Guide, Jul 2026](https://www.openlayer.com/blog/credit-scoring-eu-ai-act-compliance-guide);
[Annex III text](https://artificialintelligenceact.eu/annex/3/))

This system's `FINANCING_NEED` category flags a commercial/institutional
**legal entity** as plausibly needing a financing conversation — it does
not compute or assign a credit score to a natural person, and no
individual-level creditworthiness determination is produced anywhere in
this pipeline. Same sole-trader caveat as the GDPR section applies: if a
"client" were ever a natural person rather than a legal entity, this
conclusion would need re-checking.

## What this means for the current build — no code changes needed, one thing to keep true

Nothing here requires changing `detection_engine/external_macro_event.py`
or `datainsights/worklist.py` — the design (legal-entity-level
sector/country matching, no PEP correlation, no individual credit
scoring) already sits on the right side of both boundaries checked above.
The thing worth actively *not* doing later: don't let a future detector
combine `pep_flag` with a political/geopolitical event type without
re-reading this file first — that specific combination is where a
currently-defensible design would stop being one.
