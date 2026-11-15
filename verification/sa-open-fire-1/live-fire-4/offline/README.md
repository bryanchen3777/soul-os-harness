# live-fire-4 / offline — isolated verification artifacts

This directory holds OFFLINE verification artifacts for the B4 wiring
(`SA-TRANSPORT-OBS-2C`, mastermind Wolong approved). It is the sandbox, not
the batch:

- The formal B4 marker is `live-fire-4/sent.json` (i.e. `LIVE_DIR/sent.json`).
  It MUST NOT exist: B4 was never authorized to send.
- Offline verification markers and the secret-free case matrix
  (`case-matrix.json`, written by `test_t20_zz_matrix_artifact`) live ONLY
  here or in per-test `tmp_path` — never at the batch root.
- Every run recorded here used a fake `httpx.AsyncClient`, a fake key, and
  a fake template/preset/marker. `local POST != backend received/billed`;
  nothing here is billing evidence (`real_billing = 0`, `offline = True`).
- B1 / B2 / B3 originals are read-only and stay INCONCLUSIVE.
