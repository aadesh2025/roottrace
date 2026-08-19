# apps/sandbox-runner/python/Dockerfile — `docs/07` §4, literal.
#
# Build context is apps/sandbox-runner/ (the package root, not this file's
# own directory) — the only way COPY can reach both python/wheels/ and the
# roottrace_sandbox_runner/ package with neither living outside the context:
#   docker build -f apps/sandbox-runner/python/Dockerfile apps/sandbox-runner
#
# Digest resolved 2026-08-18 from the `python:3.12-slim-bookworm` tag. Bump
# with:
#   docker pull python:3.12-slim-bookworm
#   docker inspect --format='{{index .RepoDigests 0}}' python:3.12-slim-bookworm
# A digest change is a deliberate, reviewed deploy (`07` §4).
FROM python:3.12-slim-bookworm@sha256:a116514e19457bcb7af7efe9c3dd0b9b71e85b317694e7882a1c52aa15a78134

RUN apt-get update && apt-get install -y --no-install-recommends \
      gcc libffi-dev \
 && rm -rf /var/lib/apt/lists/*

# Pre-warmed offline wheel cache (`warm_wheels.sh`) — the V1 fixture corpus's
# own dependencies plus the pinned analysis toolchain. Not committed to git;
# built immediately before this image, same lifecycle as a lockfile-resolved
# venv.
COPY python/wheels/ /opt/wheels/

# Analysis toolchain, version-pinned so gate results are reproducible
# regardless of what apps/worker's own dev tooling happens to be on.
RUN pip install --no-index --find-links=/opt/wheels \
      pytest==8.2.0 pytest-timeout==2.3.1 pytest-json-report==1.5.0 \
      ruff==0.5.0 mypy==1.10.0 bandit==1.7.9 coverage==7.5.0

# The runner package itself — pure stdlib (apps/sandbox-runner/pyproject.toml
# deliberately declares zero dependencies), so it is copied in as source and
# invoked as a module rather than round-tripped through a wheel build. `07`
# §4's own Dockerfile shows a flat `runner.py`; the real implementation is a
# proper package instead (`apps/sandbox-runner/roottrace_sandbox_runner/`),
# which is what makes it independently unit-testable outside a container —
# `python -m roottrace_sandbox_runner.runner` is the module-invocation
# equivalent of that entrypoint.
COPY roottrace_sandbox_runner/ /opt/roottrace_sandbox_runner/
RUN chmod -R 555 /opt/roottrace_sandbox_runner

# B10 (`07` §7): the input bundle is staged here before start, outside the
# /work tmpfs, which would otherwise hide anything written under /work before
# the container starts.
RUN mkdir -p /opt/roottrace && chmod 755 /opt/roottrace

RUN useradd -u 65534 -r -s /usr/sbin/nologin sandbox || true
USER 65534:65534
WORKDIR /work

ENV PYTHONPATH=/opt
ENTRYPOINT ["python", "-m", "roottrace_sandbox_runner.runner"]
