$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
Write-Host 'ORTHO AGENT - http://127.0.0.1:8765'
Write-Host 'Configure DeepSeek in the project .env file, then restart the server.'
python -B app.py
