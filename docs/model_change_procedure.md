# Changing the LLM — the procedure (R20)

The model id lives in **one** place: the active profile's `llm.model`
(`config/profiles/<profile>.yaml`, selected by `$DATAINSIGHTS_PROFILE`,
default `fdm_local`). `agents/model_factory.py::default_model_id()` reads
it; nothing else in the code base names a model —
`tests/test_model_id_single_source.py` fails the build if a literal
model id appears anywhere outside the profiles.

A bank will change models. This is the routine, not a surprise:

1. **Pull and pin.** `ollama pull <new-id>`; set `llm.model: <new-id>` in
   every profile that should move (they need not all move together —
   `sba_local` can stay on the old model while `fdm_local` trials the
   new one). The local-only validator still applies: no `-cloud` tag, no
   non-localhost host.
2. **Re-run the golden sets.** `pytest tests/eval/test_golden_sets.py`
   with Ollama up. These are the behavioural contract for narration,
   extraction and investigation. A regression here is a *no* — the
   model does not ship, whatever its benchmark numbers say.
3. **Re-baseline the prompt hashes only if a prompt changed.**
   `prompts/hashes.json` pins prompt *text*, not the model. If the new
   model needs a prompt edit to pass step 2, that edit bumps the prompt
   `version:` and its hash together (`tests/test_prompt_versioning.py`
   tells you when they disagree).
4. **Run the guard suite.** `pytest tests/ -k "not live"` must stay green
   (it does not touch the model); then the live tests:
   `pytest tests/ -k live` with Ollama up.
5. **Record it.** Add a row to the model card
   (`docs/model_card.md` — create it on the first change with: date,
   old id → new id, golden-set pass counts before/after, any prompt
   version bumped, who approved). `narrative_source` on every
   `AgentTrace` row already carries the model label, so traces written
   before and after the change are distinguishable without any other
   bookkeeping.

What never changes with the model: categories, sizing, scores, the
worklist. The LLM narrates, extracts and proposes — it does not decide
(`docs/hardcoding_audit.md` §6). A model change therefore cannot alter a
single recommendation on the batch path, and
`tests/test_set_based_book_evaluation.py` runs with no model at all.
