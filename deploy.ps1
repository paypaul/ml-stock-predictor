Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope Process

# Install Python Dependencies
.\install_deps.ps1

# Setup the Database and begin ETL Pipeline
.\setup_pipeline.ps1

# Check the health of the data
python .\health_check.py

# Train models on tickers
python .\backtest_engine.py

# Run The dashboard
streamlit run .\app.py