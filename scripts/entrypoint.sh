#!/bin/sh
set -e
alembic -c /app/alembic.ini upgrade head
exec uvicorn backend.main:app --host 0.0.0.0 --port "${APP_PORT:-8000}"
