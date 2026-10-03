FROM python:3.12-slim-bookworm
ARG BUILD_VERSION
ARG BUILD_ARCH
LABEL io.hass.version="${BUILD_VERSION}" io.hass.type="app" io.hass.arch="${BUILD_ARCH}"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/opt/opendp3/src
WORKDIR /opt/opendp3
COPY requirements-headless.txt ./
# Every package, transitive ones included, is pinned with hashes; see
# requirements-headless.in. An unpinned or altered package fails the build.
RUN pip install --no-cache-dir --require-hashes -r requirements-headless.txt
COPY src ./src
COPY SOURCE-PROVENANCE.json /opt/opendp3/SOURCE-PROVENANCE.json
COPY run.py /opt/opendp3/run.py
COPY bluetooth_status.py /opt/opendp3/bluetooth_status.py
COPY linux_worker.py /opt/opendp3/linux_worker.py
CMD ["python", "/opt/opendp3/run.py"]
