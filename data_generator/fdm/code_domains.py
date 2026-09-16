"""Code value domains for the FDM-shaped synthetic dataset.

Every value set declares its provenance so a later SME review can tell,
without archaeology, which lists are real and which were made up here:

    SOURCE_DOCUMENTED -- the values (or the explicit semantics behind them)
        appear in the captured FDM reference, docs/fdm_reference.md.
    SOURCE_INVENTED   -- plausible placeholders. The COLUMN is real; these
        particular CODE VALUES are not confirmed against any NatWest value
        set and must be replaced or explicitly accepted during the decision
        record's Phase 7 SME validation.

`invented_domains()` exists so that review pass can enumerate exactly what
needs confirming rather than grepping for comments.
"""

from __future__ import annotations

SOURCE_DOCUMENTED = "DOCUMENTED"
SOURCE_INVENTED = "INVENTED"


class Domain:
    """One code value set plus where its values came from."""

    def __init__(self, column: str, values: list[str], source: str, note: str = ""):
        self.column = column
        self.values = values
        self.source = source
        self.note = note

    def __iter__(self):
        return iter(self.values)

    def __len__(self):
        return len(self.values)


# --- Party -----------------------------------------------------------------

PRTY_TYP_CD = Domain(
    "PRTY_TYP_CD", ["ORG", "IND"], SOURCE_DOCUMENTED,
    "DDL comment reads 'Party type (Individual, Organisation)'. C&I is "
    "organisations only, so IND is defined but never generated.",
)

PRTY_SBTYP_CD = Domain(
    "PRTY_SBTYP_CD", ["PLC", "LTD", "PRT", "COP", "PUB"], SOURCE_INVENTED,
    "Column is real; the legal-form codes are placeholders.",
)

PRTY_SGMNT_CD = Domain(
    "PRTY_SGMNT_CD", ["SME", "MID", "LRGE", "INST"], SOURCE_DOCUMENTED,
    "DDL comment reads 'Segment: SME/MID/LARGE/INST'. The join-backbone SQL "
    "in the decision record filters IN ('SME','MID','LRGE','INST'), so the "
    "4-char spelling LRGE is the one used on the wire.",
)

# Real masterscales are bank-specific and were not captured. An ordinal
# 1..10 numeric grade already exists as RSK_GRD_VAL in the DDL; these codes
# are a plausible alphanumeric companion, not a confirmed scale.
RSK_GRD_CD = Domain(
    "RSK_GRD_CD",
    ["AAA", "AA", "A", "BBB", "BB", "B", "CCC", "CC", "C", "D"],
    SOURCE_INVENTED,
    "Ordered best-to-worst. Index aligns with RSK_GRD_VAL 1..10.",
)

# --- Agreement -------------------------------------------------------------

AGRMNT_TYP_CD = Domain(
    "AGRMNT_TYP_CD", ["DEP", "LON", "ODR", "TRF", "MTG"], SOURCE_INVENTED,
    "Semantics documented ('Facility type (loan, overdraft, trade "
    "finance)'), the 3-char codes themselves are not. DEP (deposit/current "
    "account) and MTG (mortgage) added because detectors need them.",
)

AGRMNT_SBTYP_CD = Domain(
    "AGRMNT_SBTYP_CD", ["CUR", "SAV", "RCF", "TLN", "BTL"], SOURCE_INVENTED,
)

AGRMNT_RPMNT_TYP_CD = Domain(
    "AGRMNT_RPMNT_TYP_CD", ["AMT", "INT", "BUL"], SOURCE_INVENTED,
    "Amortising / interest-only / bullet.",
)

AGRMNT_RPMNT_FREQ_CD = Domain(
    "AGRMNT_RPMNT_FREQ_CD", ["MTH", "QTR", "ANN"], SOURCE_INVENTED,
)

AGRMNT_PURP_CD = Domain(
    "AGRMNT_PURP_CD",
    ["WORKCAP", "CAPEX", "REFIN", "TRADE", "PROPERTY", "GENERAL"],
    SOURCE_INVENTED,
    "VARCHAR(10) purpose code. No captured value set.",
)

PRTY_AGRMNT_ROLE_CD = Domain(
    "PRTY_AGRMNT_ROLE_CD", ["BOR", "GUA", "DIR"], SOURCE_DOCUMENTED,
    "DDL comment reads 'Role: borrower, guarantor, director'. Codes "
    "abbreviated from those documented roles.",
)

PRTY_AGRMNT_RSN_CD = Domain(
    "PRTY_AGRMNT_RSN_CD", ["NEW", "REN", "RES"], SOURCE_INVENTED,
)

MORT_REPYMNT_TYP_CD = Domain(
    "MORT_REPYMNT_TYP_CD", ["REP", "INT"], SOURCE_INVENTED,
)

# --- Event -----------------------------------------------------------------

FIN_EVNT_SBTYP_CD = Domain(
    "FIN_EVNT_SBTYP_CD", ["CRD", "DBT"], SOURCE_INVENTED,
    "Credit / debit. The real column is a VARCHAR(3) transaction sub-type "
    "with 68 further business columns not captured; this is the minimum "
    "needed to tell inflow from outflow.",
)

# Transaction-type ids. FIN_EVNT_TYP_ID is NUMBER(38,0) in the real DDL,
# so these are ids rather than codes. Mapping is invented.
FIN_EVNT_TYP_ID = Domain(
    "FIN_EVNT_TYP_ID",
    ["101", "102", "103", "201", "202", "203", "204"],
    SOURCE_INVENTED,
    "101-103 inflow types (customer receipt, contract payment, grant); "
    "201-204 outflow types (supplier, payroll, tax, loan repayment).",
)

INFLOW_TYPE_IDS = ["101", "102", "103"]
OUTFLOW_TYPE_IDS = ["201", "202", "203", "204"]

# --- Collateral (entire table set is invented) ------------------------------

CLTRL_ITEM_TYP_CD = Domain(
    "CLTRL_ITEM_TYP_CD", ["PROP", "DEBT", "CASH", "PLNT", "INVT"], SOURCE_INVENTED,
    "COLLATERAL_ITEM appears in the FDM reference only as a table name in "
    "an arrow diagram -- no column DDL was captured at all.",
)

CLTRL_VALUTN_MTHD_CD = Domain(
    "CLTRL_VALUTN_MTHD_CD", ["MKT", "IDX", "DRV"], SOURCE_INVENTED,
    "Market valuation / indexed / desktop review.",
)

# --- Shared ----------------------------------------------------------------

CURRENCIES = Domain(
    "CURY_CD", ["EUR", "GBP", "USD"], SOURCE_DOCUMENTED,
    "DDL comment on FIN_EVNT_CURY_CD reads 'Currency (EUR/GBP/USD...)'.",
)

BRND_CD = Domain(
    "BRND_CD", ["NW", "RBS", "UB"], SOURCE_DOCUMENTED,
    "PRODUCT DDL comment reads 'Brand (NW/RBS/UB)'.",
)

# Yes/no indicator columns are VARCHAR(1) throughout the captured DDL.
IND_TRUE = "Y"
IND_FALSE = "N"


ALL_DOMAINS = [
    PRTY_TYP_CD, PRTY_SBTYP_CD, PRTY_SGMNT_CD, RSK_GRD_CD,
    AGRMNT_TYP_CD, AGRMNT_SBTYP_CD, AGRMNT_RPMNT_TYP_CD, AGRMNT_RPMNT_FREQ_CD,
    AGRMNT_PURP_CD, PRTY_AGRMNT_ROLE_CD, PRTY_AGRMNT_RSN_CD,
    MORT_REPYMNT_TYP_CD, FIN_EVNT_SBTYP_CD, FIN_EVNT_TYP_ID,
    CLTRL_ITEM_TYP_CD, CLTRL_VALUTN_MTHD_CD, CURRENCIES, BRND_CD,
]


def invented_domains() -> list[Domain]:
    """Value sets Phase 7 SME validation has to confirm or replace."""
    return [d for d in ALL_DOMAINS if d.source == SOURCE_INVENTED]
