# Delete the Database
dropdb -U postgres --if-exists stock_analytics

# Delete all the models in /models
Get-ChildItem -Path "../models/" -File | Remove-Item -Force