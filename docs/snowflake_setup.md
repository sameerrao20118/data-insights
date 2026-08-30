# Snowflake setup — step by step

You have a trial account confirmed (Home page showed $400/$400 credit, 30
days remaining). This walks through everything from there to
`python -m datainsights.cli --profile snowflake_trial_ollama` actually
working. Nobody but you does this part — it needs your account and your
credentials, which never go into this repo or into chat.

Nothing here costs anything beyond your existing trial credit, and the
smallest warehouse size (XSMALL) with a short auto-suspend barely touches
it for a dataset this size.

## Step 1 — Create a warehouse

In Snowsight (left sidebar): **Compute → Warehouses → + Warehouse**
- Name: `POC_WH`
- Size: `X-Small`
- Auto-suspend: `60` seconds (so it stops billing almost immediately after
  each query)
- Auto-resume: on

Alternative (SQL worksheet, faster if you're comfortable pasting SQL):
```sql
CREATE WAREHOUSE IF NOT EXISTS POC_WH
  WAREHOUSE_SIZE = 'XSMALL'
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE;
```

## Step 2 — Create a database and schema

Open a new SQL worksheet (**+ → SQL Worksheet**, or the **+** next to
"Projects") and run:
```sql
CREATE DATABASE IF NOT EXISTS POC_DB;
CREATE SCHEMA IF NOT EXISTS POC_DB.POC_SCHEMA;
USE WAREHOUSE POC_WH;
USE DATABASE POC_DB;
USE SCHEMA POC_SCHEMA;
```

## Step 3 — Create the tables

Open `config/snowflake/ddl.sql` from this repo, copy its contents into the
same SQL worksheet (making sure `POC_DB.POC_SCHEMA` is still selected —
check the database/schema dropdowns at the top of the worksheet), and run
it. This creates three empty tables: `transactions`, `accounts`,
`balances`, with column names and types matching `config/entities.yaml`
exactly.

## Step 4 — Load the data

You have two options. Option A is simpler for a one-time load; Option B is
scriptable/repeatable.

### Option A — Snowsight's upload wizard (recommended for a first load)

For each of the three files below, from the Snowsight home page:
**Upload local files** (or **Projects → Files → Upload**):

1. Select `data_generator/output/transactions.csv` from your machine.
2. Choose database `POC_DB`, schema `POC_SCHEMA`.
3. Choose **"Select existing table"** → `TRANSACTIONS` (not "create new" —
   the table already has the right types from step 3).
4. In the column-mapping screen, confirm each CSV column maps to the
   same-named table column (it should auto-match since headers are
   identical). Header row should be detected automatically — if there's a
   "first row is header" toggle, make sure it's on.
5. Load. Repeat for `accounts.csv` → `ACCOUNTS` and `balances.csv` →
   `BALANCES`.

`transactions.csv` is ~60MB / ~303,000 rows — the wizard may take a minute
or two; that's normal.

**Do not load `trigger_events.csv`** (it's not even in the folder you're
uploading from — it lives under `protected_evaluator_only/`) — and don't
load it into this schema even later. That file is evaluation-only ground
truth; loading it somewhere the detector's connection can read defeats the
entire point of keeping it separate. If you ever want it in Snowflake too
(e.g. to run `evaluation/evaluate.py` against Snowflake-hosted data later),
put it in a **different schema** that only an evaluator role can read —
ask me when you're ready for that and I'll help set up the role/grant
split properly.

### Option B — SQL COPY INTO via an internal stage (scriptable)

```sql
CREATE OR REPLACE FILE FORMAT poc_csv_format
  TYPE = 'CSV' SKIP_HEADER = 1 FIELD_OPTIONALLY_ENCLOSED_BY = '"';

PUT file:///Users/sameera/code/datainsights/data_generator/output/transactions.csv @~/poc_stage AUTO_COMPRESS=TRUE;
PUT file:///Users/sameera/code/datainsights/data_generator/output/accounts.csv    @~/poc_stage AUTO_COMPRESS=TRUE;
PUT file:///Users/sameera/code/datainsights/data_generator/output/balances.csv    @~/poc_stage AUTO_COMPRESS=TRUE;

COPY INTO transactions FROM @~/poc_stage/transactions.csv.gz FILE_FORMAT = poc_csv_format;
COPY INTO accounts      FROM @~/poc_stage/accounts.csv.gz    FILE_FORMAT = poc_csv_format;
COPY INTO balances      FROM @~/poc_stage/balances.csv.gz    FILE_FORMAT = poc_csv_format;
```
`PUT` requires SnowSQL (the CLI) or a Snowflake Python session running
locally with file-system access — not available from the Snowsight web UI
alone. If you don't have SnowSQL installed, use Option A instead.

## Step 5 — Verify the load

Back in the SQL worksheet:
```sql
SELECT COUNT(*) FROM transactions;  -- expect ~303,000
SELECT COUNT(*) FROM accounts;      -- expect ~606
SELECT COUNT(*) FROM balances;      -- expect ~294,000
SELECT MIN(booking_date), MAX(booking_date) FROM transactions;  -- 2023-01-01 .. 2025-12-31
```
If these don't roughly match, something went wrong in the load (wrong file,
partial upload, wrong table) — fix that before moving on rather than
debugging it from the Python side.

## Step 6 — Create credentials for programmatic access

Simplest for a POC: use your own login (the `SAMEERRAO20...` /
`ACCOUNTADMIN` user visible in your screenshot) with a password. This is
fine for a trial account only you use; it is not how you'd do this in a
real bank environment (see `docs/architecture.md`'s note that key-pair
auth is the more production-like option, deferred for this POC).

If you don't already have a password set (SSO/passwordless login is
Snowflake's default for new trial accounts in some flows): **Admin →
Users & Roles** → your user → **Reset Password**, and set one.

## Step 7 — Set environment variables (never in this repo)

In your own shell profile (`~/.zshrc` or wherever you keep local exports —
**not** any file in this repo, **not** pasted into a chat with me):

```bash
export SNOWFLAKE_POC_ACCOUNT="pz07147.ap-southeast-7.aws"   # from your URL: app.snowflake.com/ap-southeast-7.aws/pz07147
export SNOWFLAKE_POC_USER="your_username"
export SNOWFLAKE_POC_PASSWORD="your_password"
export SNOWFLAKE_POC_WAREHOUSE="POC_WH"
export SNOWFLAKE_POC_DATABASE="POC_DB"
export SNOWFLAKE_POC_SCHEMA="POC_SCHEMA"
```

Then reload your shell (`source ~/.zshrc` or open a new terminal).

**On the account identifier**: Snowflake account identifiers can be
finicky — the format above (`<locator>.<region>.<cloud>`) is the most
common for accounts hosted outside us-west-2. If the connection in Step 8
fails with an account/host-not-found error, check **Admin → Accounts** in
Snowsight, which shows your exact account identifier and connection URL
directly — use whatever it shows verbatim rather than guessing from the
browser URL.

## Step 8 — Test the connection

```bash
source /Users/sameera/code/datainsights/.venv/bin/activate
python3 -c "
from datainsights.sources.snowflake_source import SnowflakeSource
from datetime import date
src = SnowflakeSource('poc', 'config/entities.yaml')
df, prov = src.read_entity('transactions', start_date=date(2023,1,1), end_date=date(2023,1,31))
print('Connected OK:', prov)
print(df.head())
src.close()
"
```
If this prints a row count and a few sample rows, the connection works end
to end — contract validation included. If it raises `EnvironmentError`,
an env var is missing (it'll name which one). If it raises a Snowflake
connection error, it's almost always the account identifier (see Step 7's
note) or the warehouse/database/schema name not matching what you created.

## Step 9 — Run the real pipeline against Snowflake

```bash
python -m datainsights.cli --profile snowflake_trial_ollama
```

Everything downstream (detector, ranking, narrative, judge, digest) is
identical to the offline run — only the source changed. Compare the digest
output and `python -m evaluation.evaluate` results against your offline
run as a sanity check; they should match closely (same underlying data,
same detector code), which is itself a useful portability check per
`docs/architecture.md`'s "Known gaps" section (a formal automated version
of this comparison — golden conformance tests — isn't built yet).

## When you're done experimenting

Suspend or drop the warehouse to stop any further compute billing:
```sql
ALTER WAREHOUSE POC_WH SUSPEND;
-- or, to remove it entirely:
DROP WAREHOUSE POC_WH;
```
Your trial credit only depletes while a warehouse is actively running a
query — with a 60-second auto-suspend and this dataset's size, actual
usage should be a small fraction of the $400 available.
