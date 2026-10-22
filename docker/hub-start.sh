#!/bin/sh
# Start the API with several workers and one metrics view across them (prometheus_client multiprocess mode).
# The directory holds per-process sample files and must be empty when the server starts.
set -eu
rm -rf "${PROMETHEUS_MULTIPROC_DIR:?}" && mkdir -p "$PROMETHEUS_MULTIPROC_DIR"
exec uvicorn hub.api.main:app --host 0.0.0.0 --port 8000 --workers "${HUB_WORKERS:-2}" --no-access-log
