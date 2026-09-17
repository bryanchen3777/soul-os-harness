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

---

## `scripts/test_*.py` → `scripts/manual_*.py`（原地改名）

Ticket: `TEST-INFRA-COLLECTION-LEAK-1` (P1, safety de-mining)。
改名日：**2026-09-16**。改名基線 commit：`ea3f95e`。
**內容一字未改**：10 支已追蹤檔在 git 中是 `R100`（逐位元等同），15 支的 sha256 前後相同。

### 為什麼是改名，而不是刪除

pytest 預設 `python_files = test_*.py`。這些腳本住在 `scripts/`，`pytest -q tests`
從來不會收集它們 —— 但**在 repo 根目錄裸跑 `pytest`** 會，因為根目錄的
collection 會往下走進 `scripts/`。而**被收集 ＝ 被 import ＝ module body 被執行**：

* 標 ⚠️ 的三支在 module body 就會有真實副作用（付費 API 呼叫／對執行中的服務開
  真實 WebSocket／寫真實 `data/`）。
* 最嚴重的是 `manual_disable_thinking.py`：module body 的 `for m in methods:` 迴圈
  直接對 `https://api.minimax.io/v1/chat/completions` 發 **6 次** POST（金鑰取自
  `.env` 的 `MINIMAX_API_KEY`）。**本票的存在理由就是它** —— 已實測：光是「被收集」
  就吃到 `HTTP 429 Token Plan usage limit`，即**收集本身在花錢**。

改名為 `manual_*.py` 後，這些腳本仍可**手動**直接執行（保留開發用途），但
`manual_*.py` 永遠不符 `python_files`，因此**永久不會被任何 pytest 收集**。

> ⚠️ **這些是手動開發腳本，部分會打真實外部 API（用你的金鑰）或需要一個已經在
> 運行的服務。勿在生產機隨意執行。** 其中 12 支會連 `ws://localhost:8000/ws`
> （對生產服務發真實訊息、寫真實對話紀錄）。

| 原名（`scripts/`） | 新名（`scripts/`） | git 追蹤 | 被收集時會執行 module body？ | 它是什麼／為何原本是地雷 |
|---|---|---|---|---|
| `test_disable_thinking.py` | `manual_disable_thinking.py` | 否（untracked） | ⚠️ **會** | minimax M2.7 的 5 種「關閉 thinking」探測。**module body 的 `for m in methods:` 迴圈直接對 `api.minimax.io` 發 6 次真實 POST** ⇒ 收集即花錢（實測 429 Token Plan usage limit）。 |
| `test_dream_event.py` | `manual_dream_event.py` | 否 | 否（`__main__` 守衛 L111） | Dream event／日記寫入驗證；載入 `.env`、透過真實 LLM 產生內容、寫真實 `data/` 日記 jsonl。 |
| `test_e2e_stage3.py` | `manual_e2e_stage3.py` | 否 | 否（`__main__` 守衛 L276） | Stage 3 e2e（`MockLLMBackend`，製程內管線，不碰伺服器）。 |
| `test_e2e_stage4.py` | `manual_e2e_stage4.py` | 否 | 否（`__main__` 守衛 L635） | Stage 4 `FishTTSHandler` e2e；`_load_api_key` 已 mock，不會真的打 TTS API。 |
| `test_full_flow.py` | `manual_full_flow.py` | 是 | 否（`__main__` 守衛 L71） | 製程內 eventbus 全流程追蹤（USER_MESSAGE → AGENT_SPEAK）。 |
| `test_full_system.py` | `manual_full_system.py` | 是 | ⚠️ **會** | **module body 尾端裸 `asyncio.run(run_tests())`（L106）**：連 `ws://localhost:8000/ws`，並開真實 `data/memory.db`。 |
| `test_group_chat.py` | `manual_group_chat.py` | 是 | 否（`__main__` 守衛 L98） | 群聊 smoke；對執行中的 `:8000` 發真實訊息。 |
| `test_group_memory.py` | `manual_group_memory.py` | 是 | 否（`__main__` 守衛 L137） | 群組記憶驗證；連 `ws://localhost:8000/ws` 與 `http://localhost:8000`。 |
| `test_m3_disable_thinking.py` | `manual_m3_disable_thinking.py` | 否 | 否（`__main__` 守衛） | minimax M3 的 4 種 thinking-disable 語法探測；`requests.post` 打 `api.minimax.io`（OpenAI 相容 ＋ `/anthropic`）。 |
| `test_memory_integrity.py` | `manual_memory_integrity.py` | 是 | 否（`__main__` 守衛 L155） | 記憶寫入／讀取完整性；對執行中的 `:8000` 開真實 WebSocket。 |
| `test_memory_persist.py` | `manual_memory_persist.py` | 是 | 否（`__main__` 守衛 L79） | `MemoryStore` 持久化 ＋ 對執行中的 `:8000` 探測。 |
| `test_memory_split.py` | `manual_memory_split.py` | 是 | 否（`__main__` 守衛 L104） | 公開／私密記憶分流驗證；對執行中的 `:8000` 開真實 WebSocket。 |
| `test_private_chat.py` | `manual_private_chat.py` | 是 | 否（`__main__` 守衛 L182） | 互動式私聊驅動器；連 `:8000`、寫真實 `data/conversations/*.json`。 |
| `test_proactive_bugs.py` | `manual_proactive_bugs.py` | 是 | 否（`__main__` 守衛 L192） | 主動訊息 bug 探測（身分混淆／空草稿／無窮迴圈）；連 `:8000`。 |
| `test_proactive_quick.py` | `manual_proactive_quick.py` | 是 | ⚠️ **會** | **module body 尾端裸 `asyncio.run(main())`（L97）**：直接對執行中的 `:8000` 開真實 WebSocket。 |

> 註：`test_public_chat*` 之類的同名歷史檔不在本次 15 支之列；上表即本次改名的完整清單。
> 未追蹤的 5 支（`manual_disable_thinking.py`／`manual_dream_event.py`／
> `manual_e2e_stage3.py`／`manual_e2e_stage4.py`／`manual_m3_disable_thinking.py`）
> 是用 `Rename-Item` 改的，其餘 10 支用 `git mv`。

### 同一張票補上的護欄

* **`pytest.ini`（`testpaths = tests`）** —— 之後在 repo 根目錄裸跑 `pytest`，
  只會收集 `tests/`，**永遠不會**走進 `scripts/` 或其他散落腳本。
* **`tests/infra/test_no_production_spawn_guard.py` 新增 R5** —— 掃描範圍內
  （根目錄 `*.py` ＋ `tests/**/*.py`）出現**任何**未列管的
  `subprocess.{Popen,run,call,check_call,check_output,getoutput,getstatusoutput}`／
  `os.system`／`os.popen`／`os.spawn*` 呼叫即紅，並以
  `EXPECTED_SUBPROCESS_ALLOWLIST_SIZE` 鎖定白名單。這封死了 R1 字面量比對抓不到的
  「變數中介」與「拆分 token」繞過面。
* **同一模組新增 `scripts/` 不得存在 `test_*.py` 的斷言** —— 本節所述的改名
  一旦被還原，護欄立刻變紅。

### `tests/_verify_*.py`（一次性 live driver）一併歸檔

26 支未追蹤的一次性 driver 已原文歸檔到本目錄（`_verify_*.py.txt`，**`.txt` 確保
永遠不會被收集**）並自 `tests/` 移除；只為它們而存在的 `:8000` 位址 allowlist 條目
同時被刪除（allowlist 由 28 筆縮到 9 筆，計數鎖定同步更新）。
