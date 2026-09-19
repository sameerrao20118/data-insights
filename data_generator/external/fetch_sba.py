"""
Fetch the SBA Paycheck Protection Program (PPP) loan-level dataset into a
GITIGNORED folder and verify the download against the file size SBA's own
CDN reports. Mirrors data_generator/external/fetch_berka.py's discipline,
for a source chosen specifically because it is NOT retail data.

What this data is -- stated plainly so nobody overclaims it later:
  - REAL, disbursed commercial loans to real US businesses, released by the
    U.S. Small Business Administration under FOIA
    (https://data.sba.gov/dataset/ppp-foia). Real borrower name, address,
    NAICS industry code, loan amount, approval date, lender, loan status,
    jobs reported.
  - This is the >=$150,000 slice (`public_150k_plus`) -- deliberately, to
    stay in small-BUSINESS rather than micro/sole-trader territory, and
    because it is a single file rather than the sub-$150k split across 13.
  - It is LOAN-LEVEL only: real entities, real sectors (NAICS), real
    facility amounts/dates/outcomes -- but no account/transaction/balance
    history exists publicly for any real commercial client, anywhere (a
    confidentiality constraint on the whole data category, not a gap in
    this search). data_generator/fdm/load_sba.py layers SYNTHETIC deposit
    activity on top of these REAL entities for that reason -- disclosed in
    every output, never presented as real transaction data.
  - Licence: U.S. government open data (FOIA release), no additional
    restriction stated on data.sba.gov. Still get a licence/legal check
    before this, or anything derived from it, goes onto a NatWest system --
    it is real third-party business data, government-open or not.

Nothing here is committed: the raw file lands in
data_generator/external/sba/ (gitignored). Re-run this script to rebuild
from scratch.

Source (data.sba.gov's own dataset page, not guessed):
  https://data.sba.gov/dataset/ppp-foia
Direct file (verified reachable, Content-Length checked below):
  https://data.sba.gov/sites/default/files/distribution/SBA-OCA-2022-07-001/public_150k_plus_240930.csv

Run: python -m data_generator.external.fetch_sba
"""

from __future__ import annotations

import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET_DIR = os.path.join(HERE, "sba")
TARGET_FILE = os.path.join(TARGET_DIR, "public_150k_plus.csv")

SOURCE_URL = ("https://data.sba.gov/sites/default/files/distribution/"
             "SBA-OCA-2022-07-001/public_150k_plus_240930.csv")

# Verified via `curl -sI` against the live file on 2026-09-17 -- if SBA
# republishes with a different size, this script fails loudly rather than
# silently accepting a different dataset under the same name.
EXPECTED_BYTES = 452_077_279

PROVENANCE = """# SBA PPP loan-level dataset (>= $150,000 slice) -- provenance

- Content: real, disbursed Paycheck Protection Program loans to real US
  businesses -- borrower name/address, NAICS industry code, loan amount,
  approval date, originating/servicing lender, loan status, jobs reported.
- Released by: U.S. Small Business Administration, under FOIA.
- Retrieved from: {url}
- Verified: downloaded file size matches the source's reported
  Content-Length ({expected:,} bytes).
- Licence: U.S. government open data. No additional restriction stated on
  data.sba.gov. Get a licence/legal check before this, or any derivative,
  goes onto a NatWest system -- real third-party business data, government-
  open or not.
- Adaptation in this project: entities, sectors (NAICS), loan amounts/
  dates/outcomes are used AS-IS (real). Deposit account activity (daily
  balances, transactions) does not exist publicly for any real commercial
  client and is SYNTHETICALLY generated on top of these real entities --
  see data_generator/fdm/load_sba.py. Never present deposit-side output as
  real transaction data.
"""


def main() -> int:
    os.makedirs(TARGET_DIR, exist_ok=True)
    print(f"Downloading {SOURCE_URL}\n  -> {TARGET_FILE}")
    req = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "datainsights-poc/1.0"})
    with urllib.request.urlopen(req) as resp, open(TARGET_FILE, "wb") as out:
        total = 0
        while chunk := resp.read(1 << 20):
            out.write(chunk)
            total += len(chunk)
            print(f"\r  {total / 1_000_000:,.0f} MB", end="", flush=True)
    print()

    actual_bytes = os.path.getsize(TARGET_FILE)
    with open(TARGET_FILE, "rb") as f:
        rows = sum(1 for _ in f) - 1  # header line

    with open(os.path.join(TARGET_DIR, "PROVENANCE.md"), "w") as f:
        f.write(PROVENANCE.format(url=SOURCE_URL, expected=EXPECTED_BYTES))

    print(f"  {actual_bytes:,} bytes ({rows:,} loan rows), expected {EXPECTED_BYTES:,} bytes")
    if actual_bytes != EXPECTED_BYTES:
        print("VERIFICATION FAILED -- file size does not match the source's reported "
             "Content-Length. Do not build on this copy; SBA may have republished the "
             "file under the same URL with different content.")
        return 1
    print("Verified against the source's reported Content-Length.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
