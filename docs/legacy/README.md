# Archived one-off audit / verification drivers

Ticket: `TEST-REPO-HYGIENE-1` (P2). Archived: **2026-09-17**. Baseline commit at
archive time: `9170688`.

These files lived untracked in `tests/`. They are archived here as **`.txt`** on
purpose — pytest's default `python_files` is `("test_*.py", "*_test.py")`, so a
`.txt` archive keeps the source readable for audit while being permanently
un-collectable. The only way a file belongs back in `tests/` is as a tracked `.py`
regression; the guard `tests/infra/test_repo_hygiene_tracked_tests.py` fails if a
collectable `tests/**` file exists without being tracked.

Each `.txt` is a **byte-for-byte** copy of the original; the sha256 below is the
sha256 of *both* the removed original and this `.txt`.

| Original path (removed) | Archive | Nature | Runnable offline? | sha256 |
|---|---|---|---|---|
| `tests/verify_miku_2_22.py` | `verify_miku_2_22.py.txt` | One-off 2026-08-04 incident audit ("miku 2:22 EDT" stale-message filter), `unittest` driver with `unittest.main()`. **Not collectable at all** (name matches neither default pattern), so archiving changes the collected node-id set by exactly 0. Superseded by the tracked `tests/test_cross_session_v1.py` / `test_cross_session_v2.py` (修法 9 changed `_is_bry_online` to take `bry_latest_ts: int`, so this driver's `proxy._is_bry_online(history_list, NOW)` call is stale and would raise). Note: importing `src.llm.proxy` has a module-level `data_root()`/`conversations` directory touch, so it is not a file to run casually on the production host. | Not run as part of the suite (not collectable). Offline mocks only if run standalone. | `f4bca26a0bbf0e3f3834b8e2fdfe325bfba574cf58e1ce68853166cc200ce2fb` |
| `tests/test_epistemic_failure_isolation.py` | `test_epistemic_failure_isolation.py.txt` | `[EH-4.2-ATOMIC-PROTOTYPE-1]` unmerged offline prototype test verifying atomic quintuple writes. Requires unmerged GraphStore patch; archived to prevent pytest collection pollution and 7 AttributeError failures. | Offline mocks only. | `68cdb08546b6cf74015ec1e42dd7ffeba312a99b32c9762bbec20005a1988250` |
| `tests/test_epistemic_quintuple_builder.py` | `test_epistemic_quintuple_builder.py.txt` | `[EH-4.2-QUINTUPLE-BUILDER]` unit tests for epistemic quintuple builder module. Archived alongside unmerged prototype module to maintain zero src additions in infra tickets. | Yes (pure logic, no I/O). | `293a9f21bdc983bcd4e95261b6a0850c050de2cc8eb9a80380e6d1b9bbaf4de1` |
| `src/memory/sage/epistemic_quintuple_builder.py` | `epistemic_quintuple_builder.py.txt` | Unmerged candidate implementation module for epistemic quintuple construction. Archived here to keep `src/**` strictly clean of unmerged prototype code during test infra stabilization. | Standalone module logic. | `7f54b0029d02c291c6571232fca1232114cdbc85fb82ee63e9dd8a1455441dd0` |

## Note on location

The pre-existing archive for root-level scripts lives in
`docs/legacy/root-scripts/` with its own `README.md`. `TEST-REPO-HYGIENE-1`
specified `docs/legacy/<original-name>.txt` for this ticket's archive, so this file
is the index for that directory. There is no overlap: `root-scripts/` holds
repo-root scripts, this directory holds files that were under `tests/`.
