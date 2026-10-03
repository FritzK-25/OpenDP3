# Working on OpenPowerstation

## Changes go through a pull request

- Never push to `main`. Work on a branch and open a pull request; CI runs on
  pull requests, not on branch pushes.
- One fix per push, and wait for its CI result before pushing the next. A
  stack of guesses pushed minutes apart leaves no run that says which change
  fixed or broke what.

## Reproduce a CI failure before fixing it

Read the failing job's log first, then reproduce it locally and show the
same check passing before you push. CI runs on `ubuntu-latest` and
`windows-latest`; a fix verified on one OS is not verified on the other.

Linux, from any machine with Docker (the same OS, Python and Qt libraries
as the runner):

```bash
docker run --rm -v "$PWD":/src:ro -e QT_QPA_PLATFORM=offscreen ubuntu:24.04 bash -c '
  apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    python3.12-venv libegl1 libgl1 libxkbcommon0 libdbus-1-3 libfontconfig1 libglib2.0-0 >/dev/null
  cp -r /src /w && cd /w && python3.12 -m venv /v && /v/bin/pip install -q -e ".[test,test-gui]"
  /v/bin/python -m pytest -q && /v/bin/python scripts/sync_ha_app.py --check \
    && /v/bin/python scripts/generate_protocol_reference.py --check'
```

## Before every commit

```bash
python -m pytest -q
python scripts/sync_ha_app.py --check
python scripts/generate_protocol_reference.py --check
ruff check .
```

After changing `src/`, `LICENSE` or the third-party notices, run
`python scripts/sync_ha_app.py` and commit the regenerated copy.

## Platform rules

- Code that needs Windows (DPAPI, WinRT) has its tests marked
  `pytest.mark.skipif(os.name != "nt", ...)`, with a non-Windows contract test
  beside it, as in `tests/test_dpapi_nonwindows.py`.
- Anything hashed or compared across machines must not depend on OS path
  ordering or line endings.
- Tests must never open a modal dialog; `tests/conftest.py` fails any test
  that does instead of letting it hang the run.

## Commit messages

Describe only what the diff contains. If the message says a file was added,
check `git show --stat` before pushing.
