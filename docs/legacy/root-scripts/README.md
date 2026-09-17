# Archived root-level `test_*.py` scripts

> ⚠️ **這些腳本會啟動第二個生產服務，或盲殺生產行程。禁止在生產機執行。**
> **DO NOT RUN these scripts on the production host.** Several of them spawn a
> *second* `scripts/run_server.py` on port `8000` (colliding with the live
> service), and one legacy revision carried a blind
> `subprocess.run(['pkill', '-f', 'run_server'])` that matches the production
> process by command-line pattern.
>
> They are kept here as **`.txt`** on purpose: pytest's `python_files` pattern is
> `test_*.py`, so a `.txt` archive preserves the source for audit while being
> permanently un-collectable. If any of these were re-added as `.py` at the repo
> root, a bare `pytest` run at the repo root would import them — and for
> `test_telegram.py`, `test_stage41_ws_ping*.py` the module body **executes on
> import** (`asyncio.run(main())` at module level), which would really start
> Telegram pollers / open a WebSocket to `:8000`.

Ticket: `TEST-INFRA-ROOT-SCRIPTS-1` (P1, safety de-mining).
Archived (removed from repo root): **2026-09-16**.
Baseline commit at removal: `6b6b572`.

Each `.txt` is a **byte-for-byte** copy of the original root file; the sha256
below is the sha256 of *both* the original `.py` and this `.txt`.

| Original root file | git-tracked | sha256 | Archive | What it did originally / why it is a landmine |
|---|---|---|---|---|
| `test_chat.py` | yes | `bc1f581aa18251d50258d2d8aaa58d18f646f9eb2a5ee9eb72110ea873524898` | `test_chat.py.txt` | Manual MiniMax chat smoke driver. Its `start_server()` runs `subprocess.Popen([sys.executable, 'scripts/run_server.py'])` → **spawns a second production service on `:8000`**, then talks to `ws://localhost:8000/ws`. |
| `test_fix.py` | yes | `feb1dcd303ddd6f2b5d709e2c7bc2dc73aad466b2077a3b601315db9b7c83454` | `test_fix.py.txt` | Verifies "only Yua responds / no prompt leak". Same `Popen([sys.executable, 'scripts/run_server.py'])` second-server spawn on `:8000`. |
| `test_minimax.py` | yes | `859171417f98b1011fc136bbb790a70b78210d63ed54c285c90824abeebd96e0` | `test_minimax.py.txt` | MiniMax WebSocket smoke driver. Same `Popen([sys.executable, 'scripts/run_server.py'])` second-server spawn on `:8000`. |
| `test_telegram.py` | yes | `6f23e363192fa6cbacf81c5f47d68922198cd459bbd7b2ab71e4eddb6ca6f4a9` | `test_telegram.py.txt` | Phase 5a Telegram smoke. **Module body ends with a bare `asyncio.run(main())` (line 71)** → merely *importing/collecting* it starts 3 real Telegram bot pollers. Zero `__main__` guard. |
| `test_ui.py` | yes | `a1cc89dc435a2b9f1065bf1c04952e91c84dbe9180fcb18a9ebf40196b3f0d29` | `test_ui.py.txt` | UI static-HTML probe. **Already de-fanged by `TEST-INFRA-UI-PKILL` (P2)**: its former module-level `pkill -f run_server` (blind production kill) plus second-`:8000` spawn are gone; it is now skip-gated (`SOUL_OS_UI_LIVE=1`) and only reads `http://localhost:8000/`. Still removed from the root per D1 (root must be collect-free); its *valid* coverage was re-implemented in-process — see `tests/integration/test_web_ui_static_serving.py`. |
| `test_stage41_e2e.py` | no | `24df5c19ad79e025065bfbdb9e32122b65500a4e47093f86071c389aeda0ca58` | `test_stage41_e2e.py.txt` | Stage 4.1 end-to-end run against a **live** server: `WS_URL = "ws://127.0.0.1:8000/ws"` plus hard-coded production paths (`data/soul/...`), sends real `USER_MESSAGE` and asserts on real `relationships.json` writes. |
| `test_stage41_relationships.py` | no | `75b97e4f7de0c0657c3fe40df4b9de167d86367c3db6ee6cee53cbf9516d8d76` | `test_stage41_relationships.py.txt` | One-off 2026-07-18 unit suite for `RelationshipsStore` (9 `test_*` functions, tmpdir-based, no server). Harmless by itself, but it sat at the repo root, so a bare root `pytest` collected it. Superseded by the tracked `tests/social/test_relationship_store.py`. |
| `test_stage41_ws_ping.py` | no | `46c240a2ceba31c645f969c218e09c9258fc11adfcfcec6aadfe80cb72823c49` | `test_stage41_ws_ping.py.txt` | Ping probe. Hard-coded `ws://127.0.0.1:8000/ws` **and** a module-level `asyncio.run(main())` (line 32) → executes on import, sending a real message to the live service. |
| `test_stage41_ws_ping2.py` | no | `0d693fe0b321c09f208c57d4aeb9d1de4e7bb440626129aa5cf66c2babb3ccf6` | `test_stage41_ws_ping2.py.txt` | Same as above (handles server ping). Hard-coded `:8000` + module-level `asyncio.run(main())` (line 35). |
| `test_stage41_ws_ping3.py` | no | `2d64bc62986700b6a377cc545283ceb96f746084e51e40ba2037905061a1170` | `test_stage41_ws_ping3.py.txt` | Same as above (+ pong/sleep). Hard-coded `:8000` + module-level `asyncio.run(main())` (line 46). |

## Why the root level mattered

`pytest -q tests` never collected these — but a **bare `pytest` at the repo root**
does, because the default `python_files = test_*.py` applies to the rootdir. For
`test_telegram.py` and the three `test_stage41_ws_ping*.py` files, collection
alone is enough to run the module body (`asyncio.run(main())`), i.e. the
"test" fires as an import side effect. That is the second-service / live-message
landmine this ticket removes.

Guardrail: `tests/infra/test_no_production_spawn_guard.py` now fails if any
`test_*.py` reappears at the repo root, or if `subprocess`/`os.system` code in
the scanned tree mentions `run_server`, `pkill`, `taskkill`, `Stop-Process` or
hard-codes the service address `:8000`.
