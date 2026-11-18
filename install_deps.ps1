# Install all required Python dependencies for the Enterprise Equity Analytics Portal
Write-Host "Upgrading pip and installing Python dependencies..." -ForegroundColor Cyan
python -m pip install --upgrade pip
pip install -r requirements.ini
Write-Host "All dependencies installed successfully!" -ForegroundColor Green