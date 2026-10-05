# Turning installation incidents into shared improvements

A report should distinguish observed measurements, inferred causes, and
unverified hardware behavior. One installation's recovery is not qualification
for every firmware or device model.

For each incident, record:

1. A generic symptom and the supported model/firmware label.
2. The evidence that distinguishes candidate causes; omit account IDs,
   serials, radio addresses, household topology, and private deployment links.
3. The smallest synthetic or replay fixture that reproduces the behavior.
4. The regression test, exact fix, and validation command/output.
5. What hardware behavior is still unqualified, and the public release carrying
   the correction. An installation can privately record when it accepted that release.

Examples already encoded in the product's tests:

| Lesson | Regression evidence |
|---|---|
| A live connection or retained state does not prove advancing measurements | `tests/test_ble_recovery.py`, `tests/test_jackery_health.py` |
| Recover only the affected session when batteries share a radio | `tests/test_linux_worker_recovery.py` |
| Device control requires explicit authority and a restricted outbound command | `tests/test_control_write.py`, `tests/test_jackery_outbound_gate.py` |
| An evidence bundle must omit private notes, identifiers, and raw captures | `tests/test_exports.py` |

Use synthetic data in public fixtures. Share sanitized evidence first; raw
archives and screenshots require individual inspection. Run publication checks
before pushing, then use a reviewed pull request and an accepted release.
