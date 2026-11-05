#!/usr/bin/env bash
set -e
[ -f .env ] || cp .env.example .env
docker compose up --build -d
echo 'Console: http://localhost:3000  Swagger: http://localhost:8000/docs'
