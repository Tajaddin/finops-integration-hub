#!/usr/bin/env bash
# Scan for secrets before a push: the whole git history, plus every file git would pick up next (tracked or
# untracked, minus .gitignore'd ones such as .env, .secrets/ and .venv/). Uses a local gitleaks if there is one,
# else the official container image.
set -euo pipefail

version=8.30.1
repo=$(git rev-parse --show-toplevel)
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
(cd "$repo" && git ls-files -co --exclude-standard -z | xargs -0 -I{} cp --parents {} "$stage/")
cp "$repo/.gitleaks.toml" "$stage/" 2>/dev/null || true

if command -v gitleaks >/dev/null 2>&1; then
  gitleaks git --redact --exit-code 1 "$repo"
  gitleaks dir --redact --exit-code 1 --config "$stage/.gitleaks.toml" "$stage"
else
  repo_mount=$repo stage_mount=$stage
  if command -v cygpath >/dev/null 2>&1; then  # Git Bash on Windows: hand Docker Windows paths, unmangled
    export MSYS_NO_PATHCONV=1
    repo_mount=$(cygpath -m "$repo") stage_mount=$(cygpath -m "$stage")
  fi
  docker run --rm -v "$repo_mount:/repo" "ghcr.io/gitleaks/gitleaks:v$version" git --redact --exit-code 1 /repo
  docker run --rm -v "$stage_mount:/stage" "ghcr.io/gitleaks/gitleaks:v$version" dir --redact --exit-code 1 \
    --config /stage/.gitleaks.toml /stage
fi
echo "secret scan clean"
