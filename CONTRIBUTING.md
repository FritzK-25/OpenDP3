# Contributing

Thanks for helping. The most useful contributions are hardware test reports,
bug reports with a minimal description, and fixes with a test.

## Ground rules

- **Never post private identifiers.** No device serials, Bluetooth addresses,
  account IDs, MQTT credentials or raw recordings in issues, pull requests or
  test fixtures. Use obviously fake values such as `AA:BB:CC:DD:EE:FF`.
- **Do not weaken the control gate.** Control stays off by default, and the
  outbound allowlist only grows with a test proving the exact bytes sent.
- **Say what you ran.** In a pull request, quote the command and its output.
  A new test should be shown to fail without the change.
- Keep claims about hardware to what you observed on real hardware.

## Development

```bash
python -m venv .venv
.venv/bin/pip install -e ".[test,test-gui]"     # Windows: .venv\Scripts\pip
.venv/bin/python -m pytest -q
```

The Home Assistant app builds from its own folder, so it carries a copy of the
sources. After changing anything under `src/`, run:

```bash
python scripts/sync_ha_app.py
```

The test suite fails if the copy is out of date.

To run the suite before every commit, install the pre-commit hook once:

```bash
python scripts/install_hooks.py
```

CI also lints with [Ruff](https://docs.astral.sh/ruff/) (`pip install ruff`,
then `ruff check .`); the rules are in `pyproject.toml`.

CI runs the same suite on Linux and Windows, builds the Windows executable
with the pinned release environment, and builds the Home Assistant app image
for amd64 and aarch64.

The application version lives in one place, `src/opendp3/__init__.py`, and is
changed with `scripts/bump_version.py`.

## Licence

By contributing you agree that your contribution is licensed under the
Apache License 2.0, as in [LICENSE](LICENSE).
