#!/usr/bin/env bash
# Prepare a queued forecast workflow to calculate on the current remote main.
# This runs before any model, weather download or publication step.
set -euo pipefail

if [[ "${GITHUB_REF:-}" != "refs/heads/main" ]]; then
  echo "[ERROR] Forecast publication is only permitted from refs/heads/main (got ${GITHUB_REF:-unset})." >&2
  exit 1
fi

# Refuse to overwrite or hide local changes that may have been produced by
# a misordered workflow step. This step must execute immediately after checkout.
if [[ -n "$(git status --porcelain)" ]]; then
  echo "[ERROR] Repository is not clean before synchronizing latest main." >&2
  exit 1
fi

# A run can spend time pending behind the other forecast writer. GitHub's
# event SHA may be stale by the time the concurrency lock becomes available.
git fetch --no-tags origin refs/heads/main:refs/remotes/origin/main
git checkout -B main refs/remotes/origin/main

local_sha="$(git rev-parse HEAD)"
remote_sha="$(git rev-parse refs/remotes/origin/main)"
if [[ "$local_sha" != "$remote_sha" ]]; then
  echo "[ERROR] Local checkout differs from the fetched main revision." >&2
  exit 1
fi
echo "[INFO] Forecast calculation starts from up-to-date main: $local_sha"

# A later independent push can still race at publication time; the existing
# non-force git push must fail closed rather than overwrite others' commits.
