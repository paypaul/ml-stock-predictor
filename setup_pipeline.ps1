# Set password for the current session to avoid interactive prompts
$env:PGPASSWORD = "pass1234"

# 1. Start the local PostgreSQL Windows Service
$pgService = Get-Service -Name "postgresql*" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -eq $pgService) {
    Write-Error "PostgreSQL service not found. Install PostgreSQL first."
    exit 1
}
if ($pgService.Status -ne "Running") {
    Write-Host "Starting $($pgService.Name)..." -ForegroundColor Cyan
    Start-Service -Name $pgService.Name
}

# Locate psql (uses PATH if available, otherwise checks standard Program Files path)
$psql = "psql"
if (-not (Get-Command $psql -ErrorAction SilentlyContinue)) {
    $psqlBin = Get-ChildItem "C:\Program Files\PostgreSQL\*\bin\psql.exe" -ErrorAction SilentlyContinue | Select-Object -Last 1
    if ($psqlBin) { $psql = $psqlBin.FullName }
}

# 2. Create the stock_analytics database if it does not exist yet
$dbExists = & $psql -h localhost -U postgres -tAc "SELECT 1 FROM pg_database WHERE datname='stock_analytics'"
if ($dbExists -ne "1") {
    Write-Host "Creating stock_analytics database..." -ForegroundColor Cyan
    & $psql -h localhost -U postgres -c "CREATE DATABASE stock_analytics;"
}

# 3. Apply the SQL schema and analytical views
Write-Host "Applying schema.sql..." -ForegroundColor Cyan
& $psql -h localhost -U postgres -d stock_analytics -f schema.sql

# 4. Run the ETL pipeline
python etl_ingest.py