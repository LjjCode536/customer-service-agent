#!/bin/sh
# Keep the container demo runnable when .env exists but has no API key.
# A real key always wins; an explicit MODEL_PROVIDER=offline is left untouched.
set -e
if [ -z "$MODEL_API_KEY" ] && [ "$MODEL_PROVIDER" != "offline" ]; then
  export MODEL_PROVIDER=offline
  echo 'MODEL_API_KEY is empty: using MODEL_PROVIDER=offline (no network).'
  echo 'Fill MODEL_API_KEY in .env to use real Embedding / Rerank.'
fi
exec "$@"
