SA-DEPLOY-PREFLIGHT-1 command log (verbatim commands, exit codes, .venv interpreter)
candidate HEAD: 6fdd38f7f8e7f12a5a9b9a67421ec879c62a9b13

[1] git rev-parse HEAD; git status --porcelain=v1 --untracked-files=all; git log --oneline -5
    HEAD == 6fdd38f (matches accepted content; proceed, no mixing).
    tracked diff vs HEAD: EMPTY. stash: 1 entry (pre-vc-audit local WIP, untouched).

[2] .venv\Scripts\python.exe -m pytest -q tests/soul_access tests/infra
    => 555 passed in 119.56s, exit 0 (bg_fa788df4).

[3] .venv\Scripts\python.exe -m pytest tests/soul_access -k mutation --collect-only -q
    => 32/162 tests collected (130 deselected), exit 0. Count > 0 per ticket.

[4] .venv\Scripts\python.exe -m pytest -q tests
    => 5 failed, 8974 passed, 32 skipped, 32 subtests passed in 974.15s (0:16:14), exit 1
    (bg_01bafd20). The 5 failures are exactly five-red-baseline.txt
    (nodeid + cause identical, zero new). SKIP-BASELINE full-repo: STABLE 32/32/0/0.

[5] .venv\Scripts\python.exe scripts/eval_lifecycle_cli.py --simulated-time 2026-11-12T03:00:00Z
    => exit 0 (bg_991adced; text report, truncated at 64KB in harness capture).
[6] .venv\Scripts\python.exe scripts/eval_lifecycle_cli.py --simulated-time 2026-11-12T03:00:00Z --json
    => exit 0 (bg_7bcb53d0). Raw stdout (UTF-16 via PowerShell redirect) saved as
    cli-2026-11-12.raw.txt (3039 lines incl. trailing `[report] ...` line);
    clean UTF-8 JSON saved as cli-2026-11-12.json. Verdict fields in results.md.

[7] tests/interfaces --collect-only -q | Select-String 'test_gap_'
    => 5 node IDs, identical to SA-5/SA-6 baseline (five-red-baseline.txt).

Not run: Notion sync (Mavis owns; ticket says do not sync).
Not done: no product/config/data writes; no process kill; no server start;
no port binds; no real model requests; no push.
