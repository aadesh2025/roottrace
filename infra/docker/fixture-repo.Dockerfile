# The synthetic repository, installed and runnable (T3.1 acceptance).
#
# Build from the REPOSITORY ROOT:
#   docker build -f infra/docker/fixture-repo.Dockerfile -t roottrace-fixture-repo .
#
# **This is not the sandbox image.** That is T6.1, eight phases away, and it
# adds gVisor, a read-only rootfs, dropped capabilities and a non-root user
# (`07` §L1-L8). T3.1's acceptance says the repo "installs and its test suite
# runs green inside the sandbox image", which cannot be checked against an
# image that does not exist yet.
#
# What this checks is the property that actually matters now, and the one T6.1
# will otherwise inherit as a surprise: the suite installs from a pinned base
# and runs with NO NETWORK. Dependencies are installed at build time; the test
# run itself is executed with `--network none` by the integration suite.
# T6.1 re-verifies against the real hardened image.

# python:3.12-slim-bookworm, the same digest the api image is pinned to.
FROM python@sha256:4fad23465a06cc5149a541fbec6f87e234a64dc0550f6bfdd2d290d8f03240df

RUN groupadd -r app && useradd -r -g app -u 10001 app

WORKDIR /repo

# Dependencies first, so the layer is cached across fixture edits.
COPY fixtures/synthetic-repo/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app fixtures/synthetic-repo/ ./

USER app

# Fail the build if the repo cannot even be imported. Without this, a broken
# fixture surfaces as a confusing test failure much later.
RUN python -c "import services.checkout, clients.tax_client, api.routes.checkout"

CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
