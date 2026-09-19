"""
CanonicalSource -- reads a physical DataSource through a Binding and
returns canonical (config/semantic_model.yaml) column names only.
docs/generalization_plan.md Phase 1.

This is additive: nothing in detection_engine/, agents/, or
external_events/ calls this yet (that rewire is tracked separately, see
docs/generalization_plan.md's Phase 1 status note). What's real today:
this class correctly reads the FDM binding against real generated data,
with as-at correctness, joins, derived fields, and value maps all
working and tested (tests/test_semantic_bindings.py).
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from datainsights.semantic.binding import Binding, ConceptBinding


class CanonicalSourceError(Exception):
    pass


class CanonicalSource:
    def __init__(self, source, binding: Binding):
        self.source = source
        self.binding = binding
        # R13 (docs/refactor_plan.md §6b): the whole-book path used to
        # re-assemble, re-project and boolean-mask the FULL entity on every
        # per-client read() -- O(clients x rows), measured at 2.4M rows
        # re-merged for 60 clients on a 22k-row set. Now the projected,
        # as-at-collapsed frame is built once per (concept, as_at) and a
        # party_id/account_id slice is a position lookup. Caches live on
        # the instance: a book run shares ONE CanonicalSource
        # (agents/orchestrator.evaluate_book), a long-lived process
        # sweeping many as-of dates constructs a fresh one per sweep.
        # Safe to hand out cached frames because pandas >= 3 is
        # copy-on-write: a caller assigning a column gets its own copy.
        self._frame_cache: dict[tuple, pd.DataFrame] = {}
        self._index_cache: dict[tuple, dict] = {}

    def available(self, concept: str) -> bool:
        cb = self.binding.concepts.get(concept)
        return cb is not None and cb.unavailable is None

    def unavailable_reason(self, concept: str) -> str | None:
        cb = self.binding.concepts.get(concept)
        return cb.unavailable if cb else f"concept {concept!r} not declared in binding {self.binding.schema_name!r}"

    def _require(self, concept: str) -> ConceptBinding:
        cb = self.binding.concepts.get(concept)
        if cb is None:
            raise CanonicalSourceError(f"concept {concept!r} not in binding {self.binding.schema_name!r}")
        if cb.unavailable:
            raise CanonicalSourceError(f"concept {concept!r} unavailable under binding "
                                       f"{self.binding.schema_name!r}: {cb.unavailable}")
        return cb

    def _read_physical(self, entity: str) -> pd.DataFrame:
        df, _ = self.source.read_entity(entity, allow_unbounded=True)
        return df

    def _assemble(self, cb: ConceptBinding) -> pd.DataFrame:
        """Base entity + every join, merged on physical columns, still in
        physical column names -- renaming happens last, in _project()."""
        df = self._read_physical(cb.entity)
        for j in cb.joins:
            other = self._read_physical(j.entity)
            other_cols = [j.on] + [phys for phys in j.fields.values() if phys not in (j.on,)]
            other_cols = [c for c in dict.fromkeys(other_cols) if c in other.columns]
            how = "left" if j.optional else "inner"
            df = df.merge(other[other_cols], on=j.on, how=how, suffixes=("", "_joined"))
        return df

    def _project(self, cb: ConceptBinding, df: pd.DataFrame) -> pd.DataFrame:
        out = pd.DataFrame(index=df.index)
        for canon_field, phys_col in cb.fields.items():
            out[canon_field] = df[phys_col] if phys_col in df.columns else None
        # Joined columns (e.g. Party.sector_code from a PARTY_DEMOGRAPHIC
        # join) are declared under joins[].fields, not cb.fields -- they
        # need projecting too, the exact bug an end-to-end read against
        # real data caught (a unit test on _project() alone would have
        # missed it, since it would have supplied already-joined input).
        for j in cb.joins:
            for canon_field, phys_col in j.fields.items():
                if canon_field not in out.columns:  # cb.fields takes precedence on a name clash
                    out[canon_field] = df[phys_col] if phys_col in df.columns else None
        for canon_field, spec in cb.derived.items():
            if spec.const is not None:
                out[canon_field] = spec.const
            elif spec.map is not None and spec.from_ in df.columns:
                out[canon_field] = df[spec.from_].map(spec.map)
                if spec.default is not None:
                    out[canon_field] = out[canon_field].fillna(spec.default)
        if cb.bitemporal:
            out["valid_from"] = pd.to_datetime(df[cb.bitemporal.valid_from])
            end = df[cb.bitemporal.valid_to].replace("", pd.NA)
            out["valid_to"] = pd.to_datetime(end, errors="coerce")
        return out

    def read(self, concept: str, *, as_at: date | None = None,
             party_id: str | None = None, account_id: str | None = None) -> pd.DataFrame:
        """Canonical-column DataFrame for `concept`. If the binding marks
        the concept bi-temporal and `as_at` is given, collapses to the
        version valid at that date (EFFECTIVE_START_DT <= as_at AND
        (EFFECTIVE_END_DT IS NULL OR EFFECTIVE_END_DT > as_at)) -- the
        exact predicate FdmLocalSource.as_at() uses, reimplemented here
        against canonical columns so it works for ANY DataSource, not
        just one with its own as_at() method."""
        cb = self._require(concept)
        out = self._frame(concept, cb, as_at)

        if party_id is not None and "party_id" in out.columns:
            out = self._slice(concept, as_at, out, "party_id", party_id)
        if account_id is not None and "account_id" in out.columns:
            out = self._slice(concept, as_at, out, "account_id", account_id)
        if out is self._frame_cache.get((concept, as_at)):
            # Unfiltered read: never hand out the cached object itself -- a
            # caller's column assignment would land in the cache. A shallow
            # copy under copy-on-write shares the data until written.
            out = out.copy(deep=False)
        return out

    def _frame(self, concept: str, cb: ConceptBinding, as_at: date | None) -> pd.DataFrame:
        """The whole-book projected frame for (concept, as_at), built once."""
        key = (concept, as_at)
        cached = self._frame_cache.get(key)
        if cached is not None:
            return cached
        out = self._project(cb, self._assemble(cb))
        if cb.bitemporal and as_at is not None:
            ts = pd.Timestamp(as_at)
            mask = (out["valid_from"] <= ts) & (out["valid_to"].isna() | (out["valid_to"] > ts))
            out = out[mask]
            out = out.drop(columns=["valid_from", "valid_to"])  # collapsed to one row per key -- redundant now
        out = out.reset_index(drop=True)
        self._frame_cache[key] = out
        return out

    def _slice(self, concept: str, as_at: date | None, frame: pd.DataFrame,
               col: str, value) -> pd.DataFrame:
        """frame[frame[col] == value] as a position lookup: the groupby
        index is built once per (concept, as_at, col), then every client
        is O(its own rows), not O(the book). Only used on the cached
        whole-book frame, whose row positions are stable."""
        key = (concept, as_at, col)
        index = self._index_cache.get(key)
        if index is None:
            index = frame.groupby(col, sort=False).indices if len(frame) else {}
            self._index_cache[key] = index
        positions = index.get(value)
        if positions is None or frame is not self._frame_cache.get((concept, as_at)):
            # value absent (empty result), or a frame already narrowed by a
            # previous filter (party_id then account_id) -- fall back to a
            # mask over what is left, which is already small.
            return frame[frame[col] == value].reset_index(drop=True)
        return frame.iloc[positions].reset_index(drop=True)

    def versions(self, concept: str, *, party_id: str | None = None,
                 account_id: str | None = None) -> pd.DataFrame:
        """Every effective-dated version, uncollapsed -- for a detector
        that needs history (rating_downgrade, revenue_pattern_change's
        prior window, ...), not just the as-at snapshot."""
        return self.read(concept, as_at=None, party_id=party_id, account_id=account_id)
