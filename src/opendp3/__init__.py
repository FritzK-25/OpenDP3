"""OpenDP3: local battery telemetry with an explicit guarded-control boundary."""
# THE single source of the application version. pyproject.toml reads it through
# setuptools' dynamic version, packaging/prepare.py generates the Windows
# version resource from it, and scripts/bump_version.py is the only thing that
# should ever edit this line.
__version__ = "0.2.0"

# The decoder schema version is NOT the application version. It is recorded into
# every session row and every evidence export, so changing it makes a claim about
# how telemetry was interpreted. It moves only when decoding behaviour changes.
DECODER_SCHEMA_VERSION = "0.1.4"
UPSTREAM_REVISION = "7cde8e5922589b5e3c81585890b5188747f5b037"
DECODER_VERSION = "dp3-mr521/" + DECODER_SCHEMA_VERSION + "+" + UPSTREAM_REVISION[:12]
