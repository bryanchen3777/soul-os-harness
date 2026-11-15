# SA-OPEN-FIRE-1 B2（live-fire-2）— 入列驗收單（唯讀核查，未開門、未發送）

- 批次ID：`SA-OPEN-FIRE-1 live-fire-2` ／ dialog_request_id `req-sa-open-fire-1-0001`（沿用原票，新批次由目錄＋sent.json 區分）
- 核查時間（UTC）：2026-11-15T00:06:04Z 前後（gate1＋gate2 同輪）
- 結論：**ADMISSION 條件綠、LIVE-FIRE BLOCKED（B1＋B2 雙獨立阻斷）—— 失敗即停，未建 marker，未耗額度**
- live-fire-1 封存不動（其 `sent.json` 未重置、未讀寫）

## 1. 入列驗收（gate1，只讀）

| 項 | 結果 | 行證 |
|---|---|---|
| HEAD 含 ed9e253 | PASS | `git rev-parse HEAD`＝`ed9e253e610795c1321007de4fc4dae7217289b2`（即收斂票本身） |
| sender 共用派送前門控生效 | PASS | `wiring.build_gated_sender` 存在（`WIRING_VERSION="salw/v1"`）；sha256 `4948899e…76487f85`（相對 live-fire-1 驗收時 `2adf8ce8…` 變化＝ed9e253 本票所加 80 行共享門，預期內） |
| 薄端控制面程式就緒 | PASS | `serve_sa_thin.ThinControl` 存在；`is_thin_open()=False`；sha256 `3eede68c…82a59e79`（相對 live-fire-1 驗收時 `890dcc65…` 變化＝ed9e253 本票所加 ThinControl，預期內） |
| 未動的程式版本（回歸一致） | PASS | `src/llm/proxy.py` sha `fcc4c668…d0928`、`src/soul_access/evidence.py` sha `a7e9dd67…271e82b` —— 與 live-fire-1 驗收單逐字一致 |
| 路由 rem/primary | PASS | registry `routes` 含 `{rem, primary, agent_rem}`（另有 minimax/glm/mimo 三個別名並存，不影響本票） |
| preset primary 釘選 | PASS | provider openai／model `deepseek-v4.1-flash:1017`／`key_ref=OLLAMA_API_KEY`／params `{temperature:0.5, max_tokens:8, base_url:https://ollama.com/v1/chat/completions}` |
| max_retries=0 | PASS | `wiring.py:353` 取證模式固定 0；`proxy.py:2133 range(max_retries+1)`＝`range(1)` 恰 1 POST |
| 併發=1、無自動探測 | PASS | 單行程單 dialog 單 POST（同步泵、無執行緒）；wiring 匯入僅 stdlib＋api/bridge/release/schema（無探測／重試／背景任務） |
| 模板同一位元組 | PASS | `scripts/sa_open_fire_probe.build_request()` 重算 sha `418af813…915eeb1` 與 `request.template.json` 內載 sha 逐字一致；`request_id=req-sa-open-fire-1-0001` |

## 2. 即時 preflight（gate2，只讀＋測試）

| 項 | 結果 |
|---|---|
| `:18765` 空 | PASS（bind 探針可綁＝空；薄端從未啟動） |
| 舊排程 | PASS（`SoulOS-ExternalGateway` Disabled；`SoulOS_Watchdog` Ready 常態） |
| `:8000` 只讀 | PASS（LISTENING PID 22832，`data/server.pid`=19512 trampoline 形狀；本輪未發任何請求、未重啟） |
| registry／presets pin 一致 | PASS（實測 registry `89c30666…759128`、presets `c37de506…58cdc1`，與 `serve_sa_thin.py` 內釘選值逐字一致） |
| 雙門 closed | PASS（`release/sa.json` 讀回 `{"status":"closed"}`；`is_thin_open()` False） |
| T13 薄端測試 | PASS（`pytest -q tests/soul_access -k t13`：21 passed，`.venv` Python 3.11.15） |
| T15 共用門控測試（附帶） | PASS（29 passed，同解器） |

見 `preflight-b2.json`。

## 3. 實彈阻斷（兩條獨立，各自停火；停在開門之前）

- **B1（無授權開門機制）**：薄端實例門是常閉設計——行程內 `_THIN_OPEN=False` 常數，**無開啟介面**（runbook §8 明載；`preflight`／`build_thin_closed_handler` 均無 opener 參數）。本批次工單寫「開門→經薄端 HTTP 發送」，但沒有指出以何種已裁決機制開啟實例門；自行以運行期賦值（monkeypatch）翻轉產品安全常數＝未經授權的設計決策，超出本 worker 範圍（僅新目錄），故拒絕發明開門器。
- **B2（薄端 HTTP 路徑結構性無 raw＋usage）**：即使實例門能開，薄端 HTTP 200 回應為 `to_chat_response(text, model)`（`src/external_gateway/app.py`）——`usage` 三欄恆為 0（註明「未接計費，不偽造 token 數」），且不含 provider 原文 raw；而 `:8000` 生產掛載（`scripts/run_server.py:806`）以 `build_dialog_factory(llm, loop, resolve_backend=None)` **未傳 `evidence_sink`**，故 firing 行程永遠拿不到「原始 CallResult raw＋原生 usage」。按本票面「缺 raw 即停」，此路徑的結論在發送前即可判定：**必停於 EVIDENCE_GAP**，而一次真實計費推理已被消耗（零證據＋耗額度＝最壞組合）。
- 對照：live-fire-1 直送 sender 路徑具備完整取證通道（送前 payload＋回後 raw/usage，`result.json` 判準完備），當時僅敗於傳輸層（exit 7）。薄端 HTTP 路徑在本輪沒有等價通道。

## 4. 終態（讀回值）

- 計費嘗試數：**0**（`live-fire-2/sent.json` 不存在＝新批額度未啟用；live-fire-1 marker 未動）
- 薄端：從未啟動（無 run_id、無 control-file、`:18765` 仍空）
- 實例門：closed（`is_thin_open()=False`，無 opener 未發明）
- 共享門：closed（`release/sa.json` 讀回 `{"status":"closed"}`，零寫入）
- 密鑰：全程只讀變數名 `OLLAMA_API_KEY`；值從未讀取、未回報、未落盤
- 最終狀態：**實彈 BLOCKED（B1＋B2，未發送，Mavis 受控 sender 限定）**
- Frozen 差異：無（`src/`／`configs/`／`scripts/`／`data/**`／被追蹤檔全零差異；僅本目錄新增 `acceptance-b2.md`＋`preflight-b2.json`；`release/sa.json` 未碰）
- 需主大腦裁決的問題（最小）：B2 若堅持薄端 HTTP 正式路徑，須另起決策票裁決 (a) 授權的薄端開門機制、(b) `:8000` live 路由掛載 `evidence_sink`（或等價 raw＋usage 回傳通道）；或確認 B2 改走 live-fire-1 同款直送 sender 路徑（取證通道現成）。
