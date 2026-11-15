# SA-GW-OLLAMA-ALIGN-1 驗收 REPORT

- 解譯器口徑：`.venv\Scripts\python.exe`（repo 內 .venv，餘同）
- 證據檔：`verification/sa-gw-ollama-align-1/full-regression-rerun.txt`（UTF-16，Tee-Object 預設編碼）、
  `verification/sa-gw-ollama-align-1/lifecycle-cli.txt`（同）

## 改動（3 處，範圍內）

1. `configs/sa/presets.json`：4 個 preset 的 `provider` 由 `openai` 全數改為 `ollama`；
   4 處 `params.base_url` 由 `https://ollama.com/v1/chat/completions` 改為根路徑 `https://ollama.com`；
   `key_ref: OLLAMA_API_KEY` 與其餘參數不變。
2. `src/external_gateway/backends.py`：`OllamaBackend._url()` 容錯收斂——
   結尾已含 `/v1/chat/completions` 直接用；結尾為 `/v1` 追加 `/chat/completions`；
   其餘根路徑追加 `/v1/chat/completions`。`_ollama_base_allowed` 未動。
3. `tests/unit/test_external_gateway_backends.py`（新建，已 `git add` 納管）：10 筆——
   build_backend 對 provider=ollama 建出 OllamaBackend；
   _url 對根路徑／尾斜線／完整路徑／`/v1` 四形均收斂為單一 `https://ollama.com/v1/chat/completions`（不斷言雙重後綴）；
   mock httpx client 驗 Bearer 頭＋JSON payload；
   dummy provider 仍 fail-closed 500；白名單未變；生產 presets.json 四筆對齊斷言（唯讀）。

## 測試結果

- 新單元測試：`pytest -q tests/unit/test_external_gateway_backends.py` → **10 passed**。
- 雙向變異：將 `build_backend` 尾部 fail-closed 改為無條件放行（探針，`MUTATION-PROBE-DO-NOT-COMMIT`）→
  `test_build_backend_unknown_provider_fails_closed_500` 轉紅（DID NOT RAISE）；復原後轉綠。
  變異殘留檢查：`git diff` 確認探針行已完全移除。
- 網關＋AST 守衛：`pytest -q tests/unit/test_external_gateway_backends.py tests/test_external_gateway_app_1.py tests/infra`
  → 初輪 466 passed＋1 fail（`test_every_collectable_test_file_under_tests_is_git_tracked`，新檔未納管所致）；
  `git add` 後重跑衛生守衛 → **8 passed**。
- 全庫回歸：`pytest -q tests --tb=short -rf` → **3 failed, 9362 passed, 32 skipped, 32 subtests passed**（1041.91s）。
  - 2 筆為本票預期後果（見下節阻斷）：`test_t12_preflight_accepts_pinned_copies`（preflight 回 2≠0）、
    `test_t12_production_files_match_pins`（WORK sha256 `35ab58be…` ≠ pin `c37de506…`）。
    實測 HEAD 版 presets.json sha256 恆等於 pin，證明 pin 釘的是舊生產配置，本票改動即 delta。
  - 1 筆 `test_concurrent_retry_exactly_one_runtime_call`（interfaces 重試併發，REPLAY vs UNCERTAIN/IN_PROGRESS）
    與本票零交集（未動 interfaces），單跑 → **1 passed**，判 flaky。
- CLI 沙盒快跑對帳：`scripts/eval_lifecycle_cli.py --simulated-time 2026-10-24T22:00:00Z` →
  **VERDICT: PASS**。四項：sidecar 3833→3833（0 筆新增、圖非空＝冪等 no-op）；
  soul_type_counts belief/value/trait/essence 全 0；關鍵反例節點 transitions=0；
  prompt_diffs 無、unexplained=0、weak_prefix 1446→1446。

## Frozen Contract / 未預期行為

- 未碰 Agency 4 stages／TriggerEnvelope／InnerLifeEvent／4 handlers／SAGE；SUPPORTS 白名單維持 {deepseek, ollama}。
- 未讀寫 data/**（`git status --porcelain -- data/` 空）；未啟伺服器／未綁埠／未對外發請求；未殺行程。
- 未預期行為 1：`tests/infra` 衛生守衛要求新測試檔先納管（已處理）。
- 未預期行為 2：Tee-Object 落檔為 UTF-16（PS 5.1 預設），REPORT 以 UTF-8 另存摘要。

## 阻斷（需主腦裁決，本票不自行處理）

- T12 生產檔 pin（`PRESETS_SHA256` 等，位於範圍外 `src/sa_thin_entry*` 模組）仍釘舊 presets.json，
  上述 2 筆失敗在 pin 重釘前不會轉綠。重釘安全 pin 屬設計決策，請主腦開後續工單處理；
  本票按「只動三處」未動 pin。
