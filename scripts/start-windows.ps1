if (-not (Test-Path .env)) { Copy-Item .env.example .env }
docker compose up --build -d
Write-Host "Console: http://localhost:3000  Swagger: http://localhost:8000/docs"
