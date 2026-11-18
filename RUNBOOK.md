**Enterprise Equity Analytics Portal — Operational Runbook**

**1. System Overview & Data Architecture**
This platform ingests daily equity market data, enforces relational integrity in PostgreSQL, executes expanding-window walk-forward validation, and serves risk telemetry via Streamlit.
* **Source Ingestion (etl_ingest.py):** Cleansing rules strip null records, verify High >= Low bounds, and upsert rows into `daily_prices` while logging execution telemetry in `etl_audit_log`.
* **Analytical SQL Layer (schema.sql):** The `vw_ticker_analytics` view computes rolling 20-day and 50-day moving averages and realized volatility using SQL window functions.
* **Forecasting Engine (backtest_engine.py):** Trains out-of-sample Random Forest models with a 5 bps transaction slippage deduction and writes results to `model_predictions`.
* **Reporting Portal (app.py):** Exposes interactive charts, SPY benchmark comparisons, feature explainability weights, and CSV exports.

**2. Routine Monitoring & Defect Triage**
Run `python health_check.py` after daily market close to verify system readiness:
* **Symptom — ETL Failure in Audit Log:** Inspect `error_message` in `etl_audit_log`. If caused by a transient upstream API timeout, re-run `python etl_ingest.py` (upserts are idempotent).
* **Symptom — Stale Price Feed (>5 Days):** Verify network connectivity and confirm whether the ticker symbol was delisted or halted. Update `is_active = FALSE` in `tickers` if delisted.
* **Symptom — Anomalous Single-Day Return (>35%):** Check if the issuer executed a stock split. Re-ingest full history for the affected symbol so adjusted close series recalculate cleanly.

**3. Escalation Procedures**
* **Independent Resolution (Developer I Scope):** Transient API timeouts, single-ticker backfill reruns, UI date-filter bugs, and minor SQL view adjustments.
* **Escalate to Senior Developer / Delivery Manager:** Persistent database lock contention, breaking upstream API schema changes, or server infrastructure outages exceeding 2 hours.

**4. AI-Assisted Development & Data Governance**
* **Verification Requirement:** All AI-generated SQL queries, feature transformations, and financial calculations must pass `pytest test_pipeline.py` prior to merging.
* **Prompt Hygiene & Compliance:** Never paste database credentials, internal connection strings, proprietary business logic, or regulated data (such as CUI, ITAR, or DFARS-restricted records) into external AI coding assistants. Use synthetic mock schemas when prompting external tools.