# Pinned by digest: Supervisor builds with --pull, so a bare tag would give each
# rebuild or rollback of one commit whatever base was newest. Dependabot proposes
# digest updates. The Python minor and Debian release (glibc 2.36) are the
# target requirements-headless.txt is compiled for; change them only together.
FROM python:3.12-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e
ARG BUILD_VERSION
ARG BUILD_ARCH
LABEL io.hass.version="${BUILD_VERSION}" io.hass.type="app" io.hass.arch="${BUILD_ARCH}"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/opt/opendp3/src
WORKDIR /opt/opendp3
COPY requirements-headless.txt ./
# Exactly the lock and nothing else: every file must match a locked hash, wheels
# only (the slim image has no compiler), and no resolver. pip check then fails
# the build if the lock is missing a dependency of anything it installed.
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: --no-deps -r requirements-headless.txt \
    && pip check
COPY src ./src
COPY THIRD_PARTY_NOTICES.md /opt/opendp3/THIRD_PARTY_NOTICES.md
COPY THIRD_PARTY_LICENSES /opt/opendp3/THIRD_PARTY_LICENSES
COPY SOURCE-PROVENANCE.json /opt/opendp3/SOURCE-PROVENANCE.json
COPY run.py /opt/opendp3/run.py
COPY bluetooth_status.py /opt/opendp3/bluetooth_status.py
COPY linux_worker.py /opt/opendp3/linux_worker.py
CMD ["python", "/opt/opendp3/run.py"]
