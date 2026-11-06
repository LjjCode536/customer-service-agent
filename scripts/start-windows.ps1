if (-not (Test-Path .env)) { Copy-Item .env.example .env }

$providerLine = Get-Content .env | Where-Object { $_ -match '^MODEL_PROVIDER=' } | Select-Object -Last 1
$keyLine = Get-Content .env | Where-Object { $_ -match '^MODEL_API_KEY=' } | Select-Object -Last 1
$provider = if ($providerLine) { $providerLine -replace '^MODEL_PROVIDER=', '' } else { '' }
$key = if ($keyLine) { $keyLine -replace '^MODEL_API_KEY=', '' } else { '' }
if ([string]::IsNullOrWhiteSpace($key) -and $provider.Trim() -ne 'offline') {
    $env:MODEL_PROVIDER = 'offline'
    Write-Host 'MODEL_API_KEY is empty: starting with MODEL_PROVIDER=offline (no network).'
    Write-Host 'Fill MODEL_API_KEY in .env to use real Embedding / Rerank.'
}

docker compose up --build -d
Write-Host "Console: http://localhost:3000  Swagger: http://localhost:8000/docs"
