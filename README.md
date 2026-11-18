# 📈 Machine Learning Stock Predictor

An end-to-end financial data pipeline, relational analytics database, and interactive reporting application built with Python, PostgreSQL, scikit-learn, and Streamlit. 

> 💡 Rather than relying on isolated notebooks or static CSV files, this platform implements a production-style workflow: automated ETL ingestion with strict source-data validation, pre-aggregated SQL window views, out-of-sample walk-forward machine learning validation, and an interactive stakeholder dashboard with operational health telemetry.

---

## 🚀 Key Engineering Highlights

* 🗄️ **Normalized Relational Data Layer (PostgreSQL):** Stores ticker metadata, daily OHLCV price history, audit logs, and model evaluations using foreign-key constraints, check rules, and composite unique indexes for idempotent upserts.
* 📊 **In-Database Analytical Views:** Uses Common Table Expressions (CTEs) and SQL window functions to compute rolling 20-day and 50-day moving averages, daily returns, and realized volatility directly inside PostgreSQL.
* ⚙️ **Automated ETL Pipeline & Audit Logging:** Extracts historical market data via yfinance, strips malformed or null records, enforces price/volume integrity rules, and logs execution metrics into an operational audit table.
* 🧠 **Expanding-Window Walk-Forward Engine:** Prevents look-ahead bias and data leakage by training Random Forest models strictly on prior historical windows, stepping forward chronologically, and deducting simulated transaction slippage (5 bps).
* 📉 **Stakeholder Reporting & Model Explainability:** Serves interactive Plotly equity curves, SPY benchmark comparisons, underwater drawdown charts, plain-language feature importance weights, and one-click CSV report exports via Streamlit.
* 🛡️ **Automated QA & CI/CD Pipeline:** Includes a pytest unit test suite verifying temporal feature isolation and data cleansing rules, a database health monitor flagging stale feeds or anomalous price spikes, and a GitHub Actions workflow.

---

## 🛠️ Tech Stack

* 🐍 **Language & Data Processing:** Python, Pandas, NumPy, yfinance
* 🐘 **Database & Storage:** PostgreSQL, psycopg2 (Normalized DDL, Window Views, Upsert Pipelines)
* 🤖 **Machine Learning & Evaluation:** scikit-learn (RandomForestRegressor, Expanding-Window Validation)
* 🎨 **Front-End & Visualization:** Streamlit, Plotly
* ✅ **Testing, DevOps & Governance:** Pytest, GitHub Actions CI, Operational Runbook

---

## 📂 Repository Structure

* 📄 **schema.sql** - PostgreSQL DDL defining normalized tables, constraints, indexes, and analytical views.
* 🔄 **etl_ingest.py** - Batch ingestion pipeline with source validation filters and operational audit logging.
* 📈 **backtest_engine.py** - Expanding-window walk-forward training engine and risk metric calculator.
* 🖥️ **app.py** - Multi-tab Streamlit dashboard for forecast visualization, benchmark comparison, and CSV exports.
* 🩺 **health_check.py** - Automated operational monitor checking ETL status, data freshness, and price anomalies.
* 🧪 **test_pipeline.py** - Unit test suite verifying ETL cleansing rules, look-ahead prevention, and drawdown math.
* 📘 **RUNBOOK.md** - Operational maintenance guide detailing architecture, defect triage, and escalation procedures.

---

## ⚡ Quickstart

1. 📦 **Install required Python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
2. **Install PostgreSql: Update the .env file with you database configuration**

3. **Initialize the PostgreSQL database and apply the schema:**
   ```powershell
   # Windows
   .\setup_pipeline.ps1
   ```

4. **Run the ETL ingestion and walk-forward backtesting engine:**
   ```powershell
   python etl_ingest.py
   ```
   ```powershell
   python backtest_engine.py
   ```

5. **Verify pipeline health and unit tests:** 
   ```powershell
   pytest test_pipeline.py -v
   ```
   ```powershell
   python health_check.py
   ```

6. **Launch the interactive reporting dashboard:**
   ```powershell
   streamlit run app.py
   ```