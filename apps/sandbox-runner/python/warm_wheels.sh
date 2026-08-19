#!/usr/bin/env bash
# Warms apps/sandbox-runner/python/wheels/ — the offline wheel cache `07` §4's
# Dockerfile bakes into the sandbox image (`COPY wheels/ /opt/wheels/`).
#
# Not committed to git (see .gitignore) — a build artefact, not source, same
# reasoning as .venv/ or a uv.lock-resolved environment. `07` §4/§5 describes
# this cache as "refreshed weekly by a scheduled job"; this script is that
# job, run here manually or by CI before `docker build`.
#
# Downloads for the CONTAINER's platform (linux/amd64, cp312), not whatever
# platform this script happens to run on — `pip download --platform` cross-
# resolves without needing to run inside Linux itself.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

rm -rf wheels
mkdir -p wheels

# A throwaway venv purely to get a real `pip` with `download` support — `uv`
# has no equivalent subcommand. Removed at the end; nothing here is meant to
# persist beyond this script's own run.
uv venv .warm-venv --python 3.12 >/dev/null
uv pip install --python .warm-venv pip >/dev/null

./.warm-venv/*/python -m pip download \
  -d wheels \
  -r requirements.in \
  --python-version 312 \
  --platform manylinux_2_17_x86_64 \
  --implementation cp \
  --abi cp312 \
  --only-binary=:all:

rm -rf .warm-venv

echo "wheels/ warmed: $(ls wheels | wc -l) files, $(du -sh wheels | cut -f1)"
