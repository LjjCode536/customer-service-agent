#!/usr/bin/env bash
set -e
[ -f .env ] || cp .env.example .env

provider=$( (grep -E '^MODEL_PROVIDER=' .env || true) | tail -1 | cut -d= -f2- )
key=$( (grep -E '^MODEL_API_KEY=' .env || true) | tail -1 | cut -d= -f2- )
if [ -z "$key" ] && [ "$provider" != "offline" ]; then
  export MODEL_PROVIDER=offline
  echo 'MODEL_API_KEY is empty: starting with MODEL_PROVIDER=offline (no network).'
  echo 'Fill MODEL_API_KEY in .env to use real Embedding / Rerank.'
fi

docker compose up --build -d
echo 'Console: http://localhost:3000  Swagger: http://localhost:8000/docs'
