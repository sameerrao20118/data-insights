"""
Fetch the Berka / PKDD'99 financial dataset into a GITIGNORED folder and
verify it against the row counts published in its own data description.

What this data is -- stated plainly so nobody overclaims it later:
  - REAL, anonymised transactions, accounts, loans and loan outcomes from a
    Czech bank, 1993-1998, released for the PKDD'99 Discovery Challenge
    (prepared by Petr Berka and Marta Sochorova).
  - The bank served PRIVATE PERSONS. It is retail data. This project uses
    its transaction/balance DYNAMICS and real loan OUTCOMES as the
    behavioural backbone, rescaled to commercial size -- see
    data_generator/fdm/load_berka.py. That adaptation is disclosed in every
    output; it is not commercial client data.
  - No licence or terms of use are stated in the dataset's description or
    in the public mirror used here. It is widely used for research and
    teaching. Treat it as internal, non-commercial proof-of-concept use
    only, and get a licence/legal check before copying it, or anything
    derived from it, onto a NatWest system.

Nothing here is committed: the raw files land in data_generator/external/
berka/ (gitignored). Re-run this script to rebuild from scratch.

Source mirror (from a public web search, not guessed):
  https://github.com/jlacko/berka-dataset
The original host (sorry.vse.cz) no longer resolves as of 2026-09-14.

Run: python -m data_generator.external.fetch_berka
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

MIRROR = "https://github.com/jlacko/berka-dataset"
HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, "berka")

# Published object counts from "PKDD'99 Discovery Challenge - Guide to the
# Financial Data Set". Files carry one header line on top of these.
EXPECTED_ROWS = {
    "account.asc": 4500,
    "client.asc": 5369,
    "disp.asc": 5369,
    "order.asc": 6471,
    "trans.asc": 1056320,
    "loan.asc": 682,
    "card.asc": 892,
    "district.asc": 77,
}

PROVENANCE = """# Berka / PKDD'99 financial dataset -- provenance

- Content: real, anonymised retail banking data from a Czech bank, 1993-1998
  (accounts, clients, dispositions, permanent orders, transactions, loans
  with repayment status, cards, district demographics).
- Prepared by: Petr Berka and Marta Sochorova, for the PKDD'99 Discovery
  Challenge (3rd European Conference on Principles and Practice of Knowledge
  Discovery in Databases, Prague, 15-18 Sept 1999).
- Retrieved from mirror: {mirror}
- Verified: row counts match the published data description.
- Licence: NONE STATED in the description or the mirror. Internal,
  non-commercial proof-of-concept use only. Obtain a licence/legal check
  before copying this data, or any derivative, onto a NatWest system.
- Adaptation in this project: retail behaviour rescaled to commercial size,
  dates shifted, firmographics calibrated from official statistics -- see
  data_generator/fdm/load_berka.py. Never present outputs as real commercial
  client data.
"""


def main() -> int:
    if os.path.isdir(TARGET):
        shutil.rmtree(TARGET)
    print(f"Cloning {MIRROR} -> {TARGET}")
    subprocess.run(["git", "clone", "--depth", "1", MIRROR, TARGET], check=True)
    shutil.rmtree(os.path.join(TARGET, ".git"), ignore_errors=True)

    problems = []
    for name, expected in EXPECTED_ROWS.items():
        path = os.path.join(TARGET, name)
        if not os.path.exists(path):
            problems.append(f"missing {name}")
            continue
        with open(path, "rb") as f:
            rows = sum(1 for _ in f) - 1  # header line
        status = "ok" if rows == expected else "MISMATCH"
        print(f"  {name:14s} {rows:>9,} rows (expected {expected:,}) {status}")
        if rows != expected:
            problems.append(f"{name}: {rows} != {expected}")

    with open(os.path.join(TARGET, "PROVENANCE.md"), "w") as f:
        f.write(PROVENANCE.format(mirror=MIRROR))

    if problems:
        print("VERIFICATION FAILED -- do not build on this copy:", "; ".join(problems))
        return 1
    print("Verified against the published data description.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
