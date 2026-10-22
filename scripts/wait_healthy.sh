#!/usr/bin/env bash
# Wait until every long-running container of the given compose profiles is healthy (or running, if it has no
# health check) and every one-shot init container exited 0. Fails with the logs of the first container that did not.
#   scripts/wait_healthy.sh [timeout-seconds]   (COMPOSE_PROFILES selects the profiles)
set -euo pipefail

timeout=${1:-600}
deadline=$((SECONDS + timeout))
while :; do
  pending=0
  # '|' and not a tab: with IFS set to whitespace, an empty Health field would collapse and shift ExitCode
  while IFS='|' read -r name state health exit_code; do
    case "$state" in
      exited)
        if [ "$exit_code" != "0" ]; then
          echo "$name exited with $exit_code"; docker logs --tail 60 "$name"; exit 1
        fi ;;
      running)
        if [ -n "$health" ] && [ "$health" != "healthy" ]; then pending=1; fi ;;
      *) pending=1 ;;
    esac
  done < <(docker compose ps -a --format '{{.Name}}|{{.State}}|{{.Health}}|{{.ExitCode}}')
  if [ "$pending" = 0 ]; then
    docker compose ps -a --format '{{.Name}}  {{.Status}}'
    exit 0
  fi
  if [ "$SECONDS" -ge "$deadline" ]; then
    echo "timed out after ${timeout}s"; docker compose ps -a; exit 1
  fi
  sleep 5
done
