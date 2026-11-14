# SA-THAW-DEPLOY-1 首段閉門驗收證據（未解凍、未部署、未開門；APPROVAL-1 仍 FROZEN）

- HEAD: `dae6fca743faafada57fb3a7e73729862ea24a66`（施工前後未動；零提交、零推送）
- 直譯器：一律 `.venv\Scripts\python.exe`（Python 3.11.15）

## 改動檔案（未提交、未推送；僅 T10 測試檔已 `git add` 以滿足 hygiene 守衛）

| 檔案 | 狀態 | SHA-256（完工時） |
|---|---|---|
| `src/soul_access/wiring.py`（純加法正式 builder） | M（已追蹤修改，未提交） | `344882797b698905aa73b89fef2e8b25ab4adca5c63631f77c96d86cc0439782` |
| `tests/soul_access/test_t10_thaw_deploy.py`（新增） | A（已 `git add` 暫存，未 commit） | `74ee0dd105d927ca243c6b1a0617909cd40c584bbd6a46e41a64fb27a06b3e8b` |
| `.gitignore` | M（基線既有修改，本票未動） | — |
| `verification/sa-thaw-deploy-1/formal/registry.a.json`（4 路由：rem×4） | 未追蹤候選 | `8c8060059b59baef041999f20b6c0a764d0069d5a1105e796dbce86758ed1644` |
| `verification/sa-thaw-deploy-1/formal/registry.union.json`（20 路由：5 souls×4） | 未追蹤候選 | `d5d76ef1d6c2d32ba579a10da1d59baa01725ddf263506cdad48ac7373f02dbc` |
| `verification/sa-thaw-deploy-1/formal/presets.json`（4 preset，全 `provider=openai`＋`.invalid` 不可路由 base URL） | 未追蹤候選 | `e57fc68f74200b4e25a9d799576fabcf5b0a935567a6522bfd19416ff568ac51` |
| `verification/sa-thaw-deploy-1/cli-slot-2026-11-12T030000Z.json` | 未追蹤證據 | `b3e652ba03b38b3b7b9cdc8f5aa0d2d5e77a8548f782d8c5363a8942fd76069d` |

正式別名：`primary, minimax-m3.1-flash, glm-5.3-flash, mimo-v2.6-flash`；A 批＝rem×4，union＝5 souls×4＝20 路由。

## 測試結果

- T10：`pytest -q tests/soul_access/test_t10_thaw_deploy.py` → **21 passed**（初跑與完工重跑一致，約 1.58s）
  - T10-0 release 矩陣／熱讀、T10-1 正式 dialog 四別名、T10-2 registry／薄 `update()`、T10-3 記憶體 ASGI E2E、T10-4 雙向變異、T10-5 禁字面自檢、T10-6 回滾演練（closed→A→closed→A∪B→closed→restore A）
- `tests/infra` hygiene：初跑 `1 failed, 398 passed`（唯一失敗係根目錄暫存 debug 探針未追蹤）；改名探針為 `.txt` 後重跑對應守衛檔 **8 passed**
- 全庫回歸：`pytest -q tests` → **1 failed, 9035 passed, 32 skipped, 32 subtests passed，1030.86s（0:17:10）**
  - 唯一失敗 `tests/interfaces/test_interface_gaps_r2.py::test_concurrent_retry_exactly_one_runtime_call`（loser 見到 REPLAY 而非 UNCERTAIN/IN_PROGRESS）為執行緒交錯時序 flake，與本票無關（本票僅加 wiring 正式 builder＋T10 新檔，未觸 Journal 並發路徑）
  - 隔離重跑該單測 **1 passed（8.31s）**；整檔重跑 **14 passed**

## 雙向變異（綠→紅→還原綠，零殘留）

- M-放鬆／M-收緊皆轉紅，還原後綠；dud 熔斷在。T10-4 覆蓋。

## CLI 沙盒快跑對帳（exit 0 / PASS）

命令：`.venv\Scripts\python.exe scripts/eval_lifecycle_cli.py --simulated-time 2026-11-12T03:00:00Z`
（另以 `--json` 重跑，JSON 存於 `cli-slot-2026-11-12T030000Z.json`，剝除尾端 `[report]` 行後為純 JSON，UTF-8）

- verdict=PASS，exit_code=0，failures=[]，mode=sandbox-only
- ① sidecar：before 3625 → after 3625，transitions=0（0 筆新轉變但圖非空 ⇒ 冪等 no-op，非失敗）
- ② 類型純度：belief/value/trait/essence 轉變數恆等於 0；expected_hits_count=0
- ③ 關鍵反例節點：key_node_transitions=0（維持 active，未轉 weakening）
- ④ Prompt 投影：prompt_diffs_unexplained=0；weak_prefix 1355→1355（增量 0 = expected 0 + pending 0）

## release 缺檔（施工前後確認，保持關閉）

- `configs/sa/registry.json`：不存在（False）
- `configs/sa/presets.json`：不存在（False）
- `release/sa.json`：存在但內容 `{"status":"closed"}`（git-ignored；回歸期間由其他測試副作用生出，T8 只讀、T9/T10 只寫 tmp；gate 讀 closed，保持關閉、未動它）
- T10 內 release 矩陣＋熱讀測試綠（沙盒內臨時開門，生產側零寫入）

## 意外行為／殘留（只回報，不自行清理）

- 根目錄暫存探針（`t10*.log/err`、`t10fhrun*.py`、`t10fh*.err/log`、`t10fileorder.txt`、`t10order.txt`、`t10real.err`）及 `tests/soul_access/t10dbg_tmp_neutered.txt` 的 `rm` 被權限閘拒（requires targets-only trash marker／invalid intents），留待父層刪除。
- 早前自 spaw 的 hung debug pytest 探針行程仍在（鐵律禁殺，inert，等宿主回收）。
- 全庫回歸曾在 ~24% 出現一個 `F`，完工確認即上述 gaps-r2 flake（隔離＋整檔重跑皆綠）。

## 未解凍／未部署／未開門確認

- 未解凍、未部署、未開門、未提交、未推送；生產掛載保持 `resolve_backend=None`；未碰 `scripts/run_server.py`、`src/world/*`、token manager；`configs/` 下未新增非 Python 檔；`data/**` 零寫入；APPROVAL-1 仍 FROZEN。

## Mavis 終驗（親跑，2026-11-14；直譯器一律 `.venv` Python 3.11.15）

- T10：`pytest -q tests/soul_access/test_t10_thaw_deploy.py` → **21 passed**（1.69s）
- T10 雙向變異：`-k mutation` → **3 passed**（0.82s；放鬆／收緊轉紅、還原綠由測試內編碼，零殘留）
- T10＋r2 併發隔離：22 passed（1.77s；r2 flake 本輪全庫內未再現）
- 三目錄：`pytest -q tests/soul_access tests/interfaces tests/infra` → **1158 passed, 1 skipped**（175.21s）
- CLI 沙盒固定槽：`--simulated-time 2026-11-12T03:00:00Z` → **exit 0、VERDICT: PASS**（sidecar 3625→3625 no-op；belief/value/trait/essence 轉變 0；key_node 0；prompt_diffs_unexplained=0；weak_prefix 1355→1355）
- 全庫：`pytest -q tests` → **9036 passed, 32 skipped, 32 subtests passed**（957.34s／0:15:57，exit 0；本輪零失敗，r2 flake 未再現）
- 凍結核對：`bridge/api/release/schema`、`journal/runtime_port`、`run_server.py`、`src/world/*` 零觸碰（diff 空）；變更集僅 `.gitignore`＋`wiring.py`（純加法）＋T10 新檔
- 探針垃圾：26 個 t10 暫存檔中 24 個經 mavis-trash 清理；`t10dbg.err`／`t10real.err`（0 位元組）被他行程咬住，按鐵律不殺行程、留置（untracked，不入提交）
- 六門結論：**全綠 → SA-THAW-DEPLOY-1 第一段 ACCEPTED，可提交；門檻狀態送臥龍確認後走第二段（解凍＋閉門部署）**

## 補證（臥龍 VETO 後，相對 5fede13；不動產品、只改 T10 測試＋本文）

臥龍指出的兩缺口原文：
1. 雙向變異未用**同一個正常 oracle**證明綠→紅→還原綠（放鬆／收緊各自寫死斷言，判準不一致）。
2. 回滾演練步驟 2／4／6 先寫 open 再 update，與 runbook §3 凍結順序（關 gate → `update()` 安裝 → 開 gate）相反，且缺少「倒序必紅」對照。

改動（僅 `tests/soul_access/test_t10_thaw_deploy.py`，行號為補證後）：
- 868–883：新增兩個 bool 共用正常 oracle `_closed_forwarder_refuses`／`_open_forwarder_admits`（沿用 `_envelope`／`sa_bridge.LiveRefused`；其他異常不吞、直接拋）。
- 886–901（放鬆變異）：closed 建 fwd → oracle 綠 → 強制 open → 同一 oracle 紅 → DUD 熔斷（sent 恰 1，否則 `pytest.fail` 停手）→ `sent.clear()` 清掉 RED 殘留 → `undo()` → oracle 回綠。
- 904–919（收緊變異）：open 建 fwd → admit-oracle 綠 → `sent.clear()`（基線 send 不污染 mutant 相）→ 強制 closed → 同一 oracle 紅 → DUD 熔斷（sent 空）→ `undo()` → oracle 回綠。
- 回滾演練步驟 2／4／6（約 1003–1055）：每次 `provider.update()` 之前先讀 release JSON 斷言 `status == "closed"`（安裝前斷言關門）；update 後沿用 `_route_names()` 快照斷言；再寫 `open`；再跑 bellwether。posts 計數語義不變（closed 下零新增；open 下按批接納）。
- 1058–1080：新增倒序必紅對照 `test_t10_wrong_order_install_while_open_leaks_detected`（獨立測試，tmp 沙盒）：先寫 open → bellwether 斷言 200 被接納（開門先行洩漏舊快照流量）→ 再 `update(union)` → 斷言安裝前 posts 已增長（洩漏發生在安裝完成之前）。以檢出洩漏為通過，註明此序列為禁行、對應 runbook §3。

為何倒序對照只能是「檢出洩漏」形式：`RegistryProvider.update` 是純快照交換、無 gate 檢查（`src/interfaces/external_auth.py:40`，frozen 語義本票不改），故倒序下 update 本身不會失敗；唯一可觀測的破線訊號是「開門先行期間舊快照流量已被接納」（posts 在安裝完成前已增長），即凍結 oracle「開門前零接納」不變式被違反。測試不斷言 update 拋錯（那會與 frozen 語義矛盾），只斷言洩漏被檢出。

驗證：`pytest -q tests/soul_access/test_t10_thaw_deploy.py` → **22 passed**（.venv Python 3.11.15）；`git diff --name-only` 僅本票上述測試檔＋本文；T10-5 禁字面自檢全程通過（無新增禁字面）。

## Mavis 親驗（補證後，2026-11-14；直譯器一律 `.venv` Python 3.11.15）

- T10：`pytest -q tests/soul_access/test_t10_thaw_deploy.py` → **22 passed**（1.54s；21 原有＋1 新增倒序對照）
- 三目錄：`pytest -q tests/soul_access tests/interfaces tests/infra` → **1159 passed, 1 skipped**（163.72s）
- 全庫：`pytest -q tests` → **9036 passed、32 skipped、32 subtests passed＋1 failed**（960.96s／0:16:00）；唯一失敗仍為已知 r2 併發 flake（`test_concurrent_retry_exactly_one_runtime_call`，loser 見 REPLAY 時序競態，本票僅動 T10＋本文、未觸 Journal 並發路徑）→ 隔離重跑 **1 passed**（8.41s）→ 整檔重跑 **14 passed**（1.26s），已排除產品回歸
- CLI 沙盒固定槽：`--simulated-time 2026-11-12T03:00:00Z` → **exit 0、VERDICT: PASS**（sidecar 3625→3625 no-op；其餘四項對帳全合）
- 變更集（相對 5fede13）：僅 T10＋本文；`src/`、frozen、`data/**`、生產全零觸碰
- 補證結論：兩缺口已按臥龍最小補單關閉 → 送臥龍複裁第二段放行
