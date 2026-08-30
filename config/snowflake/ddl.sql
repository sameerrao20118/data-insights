-- DDL for the three tables config/entities.yaml currently contracts:
-- transactions, accounts, balances. Column names/types match that
-- contract's required_columns + optional_columns exactly -- if you rename
-- a column here, update entities.yaml too, or reads will fail with a clear
-- "missing required contract columns" error rather than silently working.
--
-- Run this in a Snowsight SQL worksheet, in the database/schema you create
-- for this POC. See docs/snowflake_setup.md for the full walkthrough this
-- file is step 3 of.
--
-- Unquoted identifiers are used throughout on purpose: Snowflake folds
-- these to uppercase internally but resolves lowercase references
-- case-insensitively, matching how the Python adapter code queries them.

CREATE TABLE IF NOT EXISTS transactions (
    transaction_id              VARCHAR NOT NULL,
    account_id                  VARCHAR NOT NULL,
    client_id                   VARCHAR NOT NULL,
    booking_date                DATE    NOT NULL,
    value_date                  DATE    NOT NULL,
    amount                      NUMBER(18,2) NOT NULL,
    currency                    VARCHAR(3)   NOT NULL,
    direction                   VARCHAR      NOT NULL,
    category                    VARCHAR      NOT NULL,
    channel                     VARCHAR      NOT NULL,
    counterparty_id             VARCHAR,
    counterparty_country        VARCHAR,
    high_risk_counterparty_flag BOOLEAN,
    iso20022_purpose_code       VARCHAR,
    message_type                VARCHAR,
    remittance_info             VARCHAR,
    PRIMARY KEY (transaction_id)
);

CREATE TABLE IF NOT EXISTS accounts (
    account_id     VARCHAR NOT NULL,
    client_id      VARCHAR NOT NULL,
    currency       VARCHAR NOT NULL,
    account_type   VARCHAR NOT NULL,
    is_primary     BOOLEAN NOT NULL,
    account_status VARCHAR NOT NULL,
    close_date     DATE,
    credit_limit   NUMBER(18,2),
    open_date      DATE NOT NULL,
    PRIMARY KEY (account_id)
);

CREATE TABLE IF NOT EXISTS balances (
    account_id       VARCHAR NOT NULL,
    date             DATE    NOT NULL,
    opening_balance  NUMBER(18,2) NOT NULL,
    closing_balance  NUMBER(18,2) NOT NULL,
    PRIMARY KEY (account_id, date)
);

-- Sanity checks to run after loading data (step 4 of the walkthrough).
-- Expected row counts for the default seed-42 dataset are in the comments;
-- your numbers should be close (exact match only if you loaded the exact
-- CSVs from data_generator/output/ without re-running the generator).
--
-- SELECT COUNT(*) FROM transactions;  -- ~303,000
-- SELECT COUNT(*) FROM accounts;      -- ~606
-- SELECT COUNT(*) FROM balances;      -- ~294,000
-- SELECT MIN(booking_date), MAX(booking_date) FROM transactions;  -- 2023-01-01 .. 2025-12-31
