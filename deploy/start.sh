#!/bin/sh
# Container entry point: migrate the database, (re)seed the demo if configured, then serve.
set -eu

python -m alembic -c backend/alembic.ini upgrade head

if [ -n "${SOURCED_DEMO_ADMIN_EMAIL:-}" ]; then
    # The demo is optional: a seeding problem is logged but doesn't keep the API down.
    python -m backend.app.seed_demo || echo "Demo seeding failed; starting without the demo." >&2
fi

exec python -m uvicorn backend.app.main:app \
    --host 0.0.0.0 --port "${PORT:-8000}" \
    --no-proxy-headers --log-config backend/logging.json
