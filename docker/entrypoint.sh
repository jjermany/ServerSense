#!/bin/sh
set -eu
cd /app/backend
alembic upgrade head
exec uvicorn serversense.main:app --host 0.0.0.0 --port 8080 --proxy-headers --forwarded-allow-ips="${SERVERSENSE_FORWARDED_ALLOW_IPS:-127.0.0.1}"

