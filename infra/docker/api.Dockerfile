# RootTrace AI — `api` runtime image (docs/13 §3).
#
# Build from the REPOSITORY ROOT, not this directory:
#   docker build -f infra/docker/api.Dockerfile -t roottrace-api .
# The build needs uv.lock and the workspace root pyproject.toml, both of which
# live above this file.
#
# Rules from `13` §3, each enforced below:
#   - base image pinned by DIGEST, not tag — a tag can be re-pointed
#   - multi-stage, so no build toolchain reaches the runtime layer
#   - non-root, fixed UID
#   - no secrets in any layer

# python:3.12-slim-bookworm. Bump with:
#   docker buildx imagetools inspect python:3.12-slim-bookworm \
#     --format '{{println .Manifest.Digest}}'
FROM python@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2 AS builder

WORKDIR /build

# uv itself is pinned. An unpinned installer defeats the point of a locked
# dependency set, since it is the thing resolving it.
COPY --from=ghcr.io/astral-sh/uv:0.9.9 /uv /usr/local/bin/uv

# `--no-dev` keeps pytest, ruff and mypy out of the runtime image: less to
# audit, and nothing that can execute arbitrary code from a test fixture.
#
# `--package roottrace-api` is load-bearing. `13` §3's recipe exports the
# workspace ROOT, whose `dependencies` list is empty — every member is wired in
# through the `dev` group — so `--no-dev` exported nothing at all, and
# `uv pip install -r` on an empty file succeeded. The image built, reported
# success, and died at startup with `uvicorn: not found`. Doc corrected.
#
# `--no-emit-workspace` drops the local package itself; its source is COPYed
# into the runtime layer below rather than installed.
COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/
COPY apps/worker/pyproject.toml apps/worker/
COPY apps/sandbox-runner/pyproject.toml apps/sandbox-runner/
COPY packages/sdk-python/pyproject.toml packages/sdk-python/
RUN uv export --frozen --no-dev --package roottrace-api \
      --no-emit-workspace --no-hashes > requirements.txt \
 && grep -q '^fastapi==' requirements.txt \
 && uv pip install --system --no-cache -r requirements.txt

FROM python@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2

# Fixed UID, so a volume mounted from the host has predictable ownership and
# the container cannot be silently rebuilt as root by a later base image.
RUN groupadd -r app && useradd -r -g app -u 10001 app

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

WORKDIR /app
COPY --chown=app:app apps/api ./

USER app
EXPOSE 8000

# Fail the BUILD if the runtime layer is incomplete. Without this the previous
# defect shipped an image that built cleanly and could not start — the failure
# only appeared at `docker run`, in whatever environment ran it first.
RUN uvicorn --version && python -c "import roottrace_api.serve"

# Liveness only — `/health` deliberately does not touch Postgres, so a database
# blip does not turn into a restart storm.
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import httpx,sys; sys.exit(0 if httpx.get('http://localhost:8000/health').status_code==200 else 1)"

# Not `uvicorn ... --factory` directly (as `13` §3 showed): uvicorn emits its
# first lines before it builds the app, so those escape the processor chain and
# arrive as unparseable plain text at every boot. `serve.py` validates settings,
# installs the chain, and only then starts the server. Doc corrected.
CMD ["python", "-m", "roottrace_api.serve"]
