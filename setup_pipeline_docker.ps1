# 1. Start PostgreSQL in Docker
docker run --name stock-postgres `
  -e POSTGRES_PASSWORD=postgres `
  -e POSTGRES_DB=stock_analytics `
  -p 5432:5432 -d postgres:16-alpine

# 2. Apply the SQL schema and analytical views
# (PowerShell does not support native '<' redirection, so we pipe Get-Content instead)
Get-Content schema.sql | docker exec -i stock-postgres psql -U postgres -d stock_analytics

# 3. Install dependencies and run the ETL pipeline
pip install yfinance pandas psycopg2-binary
python etl_ingest.py